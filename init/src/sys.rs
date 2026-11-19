//! Every `unsafe` in the init lives here ([0005]): the C entry point and the
//! signal calls rustix does not wrap. Each item is a safe wrapper, and
//! nothing outside this module calls libc.
//!
//! [0005]: ../../docs/decisions/0005-rust-and-unsafe.md

use std::io;
use std::mem::MaybeUninit;
use std::os::fd::{FromRawFd, OwnedFd};
use std::ptr;

use crate::signals;

// The signal table uses Linux's generic numbers and signalfd's fixed record
// size. Fail the build on a target where libc disagrees.
const _: () = {
    assert!(libc::SIGINT == signals::SIGINT as libc::c_int);
    assert!(libc::SIGUSR2 == signals::SIGUSR2 as libc::c_int);
    assert!(libc::SIGTERM == signals::SIGTERM as libc::c_int);
    assert!(libc::SIGCHLD == signals::SIGCHLD as libc::c_int);
    assert!(size_of::<libc::signalfd_siginfo>() == signals::SIGINFO_SIZE);
};

/// The program's entry point, called by the C runtime.
///
/// Rust's usual `main` wrapper opens `/dev/null` on any of fds 0 to 2 that
/// is closed, and aborts if it cannot. On our image all three are closed when
/// PID 1 starts: the root has no device nodes, so the kernel could not open
/// `/dev/console`, and `/dev/null` does not exist until PID 1 mounts `/dev`.
/// The wrapper would abort, and the kernel panic, before the init ran a line.
/// So the binary is `no_main` and starts here.
#[cfg(not(test))]
// SAFETY: nothing else in the program defines the symbol `main`: the crate is
// `no_main`, and no dependency defines one.
#[unsafe(no_mangle)]
extern "C" fn main(_argc: libc::c_int, _argv: *const *const libc::c_char) -> libc::c_int {
    crate::entry()
}

/// The full signal set, as glibc defines it.
pub(crate) struct SignalSet(libc::sigset_t);

/// Blocks every signal for the calling thread, and so for every thread it
/// starts later. glibc's `sigfillset` leaves out the two real-time signals
/// glibc uses internally (thread cancellation and `setxid` across threads),
/// so blocking this set does not break its thread machinery.
pub(crate) fn block_all_signals() -> io::Result<SignalSet> {
    let mut set = MaybeUninit::<libc::sigset_t>::uninit();
    // SAFETY: `set` is valid, writable storage for one sigset_t, which
    // sigfillset initialises in full.
    if unsafe { libc::sigfillset(set.as_mut_ptr()) } != 0 {
        return Err(io::Error::last_os_error());
    }
    // SAFETY: sigfillset returned 0, so it initialised `set`.
    let set = unsafe { set.assume_init() };
    // SAFETY: `set` is an initialised sigset_t that outlives the call, and a
    // null pointer for the old mask is allowed.
    let rc = unsafe { libc::pthread_sigmask(libc::SIG_BLOCK, &set, ptr::null_mut()) };
    if rc != 0 {
        return Err(io::Error::from_raw_os_error(rc));
    }
    Ok(SignalSet(set))
}

/// A non-blocking, close-on-exec signalfd for `set`.
pub(crate) fn signalfd(set: &SignalSet) -> io::Result<OwnedFd> {
    // SAFETY: -1 asks for a new descriptor, and `set.0` is an initialised
    // sigset_t that outlives the call.
    let fd = unsafe { libc::signalfd(-1, &set.0, libc::SFD_NONBLOCK | libc::SFD_CLOEXEC) };
    if fd < 0 {
        return Err(io::Error::last_os_error());
    }
    // SAFETY: signalfd returned a new descriptor that nothing else owns.
    Ok(unsafe { OwnedFd::from_raw_fd(fd) })
}
