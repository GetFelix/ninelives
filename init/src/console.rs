//! The console sink and the panic hook (init.md, "State reports" and
//! "Watchdog and panics").

use std::fmt::Write as _;
use std::io::{stderr, stdin, stdout};
use std::os::fd::{AsFd, AsRawFd, IntoRawFd, OwnedFd};
use std::sync::OnceLock;

use rustix::fs::{Mode, OFlags, open};
use rustix::io::{fcntl_dupfd_cloexec, fcntl_getfd, write};

use crate::clock;
use crate::record::{self, FixedBuf, Header, Record};

/// A second handle on the console for the panic hook, which cannot borrow
/// the loop's.
static PANIC_CONSOLE: OnceLock<OwnedFd> = OnceLock::new();

pub(crate) struct Console {
    fd: Option<OwnedFd>,
    boot: Option<String>,
    buf: Vec<u8>,
    dropped: u64,
}

impl Console {
    /// Before `/dev` is mounted there is nothing to write to. Records are
    /// counted as dropped (I16).
    pub(crate) fn absent() -> Console {
        Console {
            fd: None,
            boot: None,
            buf: Vec::with_capacity(512),
            dropped: 0,
        }
    }

    /// Opens the console once `/dev` is mounted. Fd 1 is the console by then
    /// ([`fill_stdio`]), or whatever started the init in a test, so the sink
    /// is a non-blocking duplicate of it. `/dev/kmsg` is the fallback when
    /// there is no console.
    pub(crate) fn open(stdout_is_console: bool) -> Console {
        let mut console = Console::absent();
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
        console.fd = fd;
        console
    }

    pub(crate) fn set_boot_id(&mut self, boot: Option<String>) {
        self.boot = boot;
    }

    /// Writes one record without blocking. A record that does not fit is
    /// dropped and counted, and the next one written carries the count.
    pub(crate) fn write(&mut self, record: &Record) {
        self.buf.clear();
        let header = Header {
            boot: self.boot.as_deref(),
            mono: clock::monotonic(),
            wall: clock::realtime(),
            dropped: self.dropped,
        };
        record::encode(&mut self.buf, &header, record);
        let written = self.fd.as_ref().and_then(|fd| write(fd, &self.buf).ok());
        if written == Some(self.buf.len()) {
            self.dropped = 0;
        } else {
            self.dropped = self.dropped.saturating_add(1);
        }
    }
}

fn set_nonblocking(fd: &OwnedFd) -> rustix::io::Result<()> {
    let flags = rustix::fs::fcntl_getfl(fd)?;
    rustix::fs::fcntl_setfl(fd, flags | OFlags::NONBLOCK)
}

/// Opens the console on any of fds 0 to 2 that is closed, as the kernel does
/// for PID 1 when the root has `/dev/console`. Ours has no device nodes, so
/// the init does it once `/dev` is mounted. Until then the three are free,
/// and the next descriptors the init opened would land on them, where any
/// write to stdout or stderr (std's own, on an allocation failure) would go.
///
/// Returns false when fd 1 had to be `/dev/null` because there is no console.
pub(crate) fn fill_stdio() -> bool {
    let closed = [
        fcntl_getfd(stdin().as_fd()).is_err(),
        fcntl_getfd(stdout().as_fd()).is_err(),
        fcntl_getfd(stderr().as_fd()).is_err(),
    ];
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
        let opened =
            console.or_else(|_| open("/dev/null", OFlags::RDWR | OFlags::CLOEXEC, Mode::empty()));
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
        let mut line = FixedBuf::<3200>::new();
        record::encode_panic(&mut line, clock::monotonic(), message.as_bytes());
        if let Some(fd) = PANIC_CONSOLE.get() {
            let _ = write(fd, line.as_bytes());
        }
    }));
}
