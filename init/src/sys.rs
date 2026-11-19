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
/// is closed, and aborts if it cannot. The kernel opens the console on them
/// from its built-in rootfs before mounting ours, so they are closed only on
/// a machine with no console device. On that machine our root, which has no
/// device nodes, has no `/dev/null` either until PID 1 mounts `/dev`, so the
/// wrapper would abort and the kernel panic before the init ran a line. The
/// binary is `no_main` and starts here, so that machine still boots, with
/// records going to `/dev/kmsg`. The qemu boot (#2) shows the usual case.
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
    // Zeroed first: the all-zero bytes are a valid, empty sigset_t on every
    // libc, whatever part of it sigfillset writes.
    let mut set = MaybeUninit::<libc::sigset_t>::zeroed();
    // SAFETY: `set` points to writable storage for one sigset_t.
    if unsafe { libc::sigfillset(set.as_mut_ptr()) } != 0 {
        return Err(io::Error::last_os_error());
    }
    // SAFETY: sigset_t is plain integers, so the zeroed storage was already a
    // valid value, and sigfillset only wrote more of them.
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
