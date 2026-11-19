//! The console sink and the panic hook (init.md, "State reports" and
//! "Watchdog and panics").

use std::fmt::Write as _;
use std::io::{stderr, stdin, stdout};
use std::os::fd::{AsFd, AsRawFd, BorrowedFd, IntoRawFd, OwnedFd};
use std::sync::OnceLock;

use rustix::fs::{Mode, OFlags, open};
use rustix::io::{FdFlags, fcntl_dupfd_cloexec, fcntl_getfd, fcntl_setfd, write};

use crate::clock;
use crate::record::{self, FixedBuf, Record, Sink};

/// A second handle on the console for the panic hook, which cannot borrow
/// the loop's.
static PANIC_CONSOLE: OnceLock<OwnedFd> = OnceLock::new();

pub(crate) struct Console {
    fd: Option<OwnedFd>,
    sink: Sink,
}

impl Console {
    /// Before `/dev` is mounted there is nothing to write to. Records are
    /// counted as dropped (I16).
    pub(crate) fn absent() -> Console {
        Console {
            fd: None,
            sink: Sink::new(),
        }
    }

    /// Opens the console after the `/dev` attempt. Fd 1 is the console by
    /// then, as the kernel opened it or [`fill_stdio`] did, or whatever
    /// started the init in a test, so the sink is a duplicate of it.
    /// `/dev/kmsg` is the fallback when there is no console.
    ///
    /// The duplicate is set non-blocking, which also sets fds 0 to 2: the
    /// kernel opens the console once for all three. That is intended. Only
    /// std writes to them, on its abort path, and losing that message to a
    /// full console is better than PID 1 blocking on it (I14).
    pub(crate) fn open(stdout_is_console: bool) -> Console {
        let fd = stdout_is_console
            .then(|| fcntl_dupfd_cloexec(stdout().as_fd(), 3).ok())
            .flatten()
            .filter(|fd| set_nonblocking(fd).is_ok())
            .or_else(|| {
                open(
                    "/dev/kmsg",
                    OFlags::WRONLY | OFlags::NONBLOCK | OFlags::CLOEXEC,
                    Mode::empty(),
                )
                .ok()
            });
        if let Some(fd) = &fd
            && let Ok(dup) = fcntl_dupfd_cloexec(fd, 3)
        {
            let _ = PANIC_CONSOLE.set(dup);
        }
        Console {
            fd,
            sink: Sink::new(),
        }
    }

    pub(crate) fn set_boot_id(&mut self, boot: Option<String>) {
        self.sink.set_boot_id(boot);
    }

    /// Writes one record without blocking; see [`Sink::emit`].
    pub(crate) fn write(&mut self, record: &Record) {
        let fd = self.fd.as_ref();
        self.sink
            .emit(clock::monotonic(), clock::realtime(), record, |bytes| {
                fd.and_then(|fd| write(fd, bytes).ok())
            });
    }
}

fn set_nonblocking(fd: &OwnedFd) -> rustix::io::Result<()> {
    let flags = rustix::fs::fcntl_getfl(fd)?;
    rustix::fs::fcntl_setfl(fd, flags | OFlags::NONBLOCK)
}

/// Makes sure fds 0 to 2 are open, and marks all three close-on-exec.
///
/// The kernel opens the console on them before it mounts the root, from its
/// built-in rootfs, so they are closed only on a machine with no console
/// device. Then the next descriptors the init opened (the signalfd, the
/// worker's eventfd) would land on them, where any write to stdout or
/// stderr, std's own on an allocation failure, would go. So a closed one gets
/// the console, else `/dev/null`, else an `O_PATH` handle on `/`, which no
/// write can go through.
///
/// Close-on-exec on all three, the kernel's included, because init.md makes
/// every init fd close-on-exec: nothing PID 1 holds reaches a launcher or a
/// service by accident (#48 gives services their own 0 to 2).
///
/// Returns false when fd 1 is not the console.
pub(crate) fn fill_stdio() -> bool {
    let (stdin, stdout, stderr) = (stdin(), stdout(), stderr());
    let fds: [BorrowedFd<'_>; 3] = [stdin.as_fd(), stdout.as_fd(), stderr.as_fd()];
    let closed = fds.map(|fd| fcntl_getfd(fd).is_err());
    let mut stdout_is_console = true;
    for (expected, closed) in closed.into_iter().enumerate() {
        if !closed {
            continue;
        }
        let console = open(
            "/dev/console",
            OFlags::RDWR | OFlags::NOCTTY | OFlags::CLOEXEC,
            Mode::empty(),
        );
        let is_console = console.is_ok();
        let opened = console
            .or_else(|_| open("/dev/null", OFlags::RDWR | OFlags::CLOEXEC, Mode::empty()))
            .or_else(|_| open("/", OFlags::PATH | OFlags::CLOEXEC, Mode::empty()));
        // open returns the lowest free descriptor, which is `expected` when
        // everything below it is open. Kept open for the life of PID 1.
        match opened {
            Ok(fd) if usize::try_from(fd.as_raw_fd()).ok() == Some(expected) => {
                let _ = fd.into_raw_fd();
                if expected == 1 {
                    stdout_is_console = is_console;
                }
            }
            _ if expected == 1 => stdout_is_console = false,
            _ => {}
        }
    }
    for fd in fds {
        let _ = fcntl_setfd(fd, FdFlags::CLOEXEC);
    }
    stdout_is_console
}

/// Puts a panic's message on the console. The build sets `panic = "abort"`:
/// PID 1 has no caller to unwind to and must not exit (I1), and a panic is a
/// bug, not an error the init can handle. So the hook reports, the process
/// aborts, the kernel panics on the death of init, and `panic=10` on the
/// command line reboots the machine.
pub(crate) fn install_panic_hook() {
    std::panic::set_hook(Box::new(|info| {
        // Fixed buffers: the hook must not depend on the heap.
        let mut message = FixedBuf::<512>::new();
        let _ = write!(message, "{info}");
        let line = record::panic_line(clock::monotonic(), message.as_bytes());
        if let Some(fd) = PANIC_CONSOLE.get() {
            let _ = write(fd, line.as_bytes());
        }
    }));
}
