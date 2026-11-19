//! What each signal means to PID 1 (init.md, "Signals and stop requests").

use crate::machine::StopKind;

// Linux's generic signal numbers, shared by x86_64 and aarch64. signalfd
// reports these, so the mapping must not depend on the build host's numbering.
// `sys` checks them against libc at compile time.
pub(crate) const SIGINT: u32 = 2;
pub(crate) const SIGUSR2: u32 = 12;
pub(crate) const SIGTERM: u32 = 15;
pub(crate) const SIGCHLD: u32 = 17;

/// Size of `struct signalfd_siginfo`, fixed by the kernel ABI.
pub(crate) const SIGINFO_SIZE: usize = 128;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Meaning {
    /// A child exited: reap until none is left (I2).
    Reap,
    /// An ordered shutdown ending in this action.
    Stop(StopKind),
    /// Recorded and otherwise ignored.
    Ignore,
}

/// The signal table: four signals, three meanings, everything else ignored.
pub(crate) fn meaning(signo: u32) -> Meaning {
    match signo {
        SIGCHLD => Meaning::Reap,
        // SIGINT is Ctrl-Alt-Del, once the kernel is told not to reboot on it.
        SIGINT | SIGTERM => Meaning::Stop(StopKind::Reboot),
        SIGUSR2 => Meaning::Stop(StopKind::PowerOff),
        _ => Meaning::Ignore,
    }
}

/// The signal number from one `struct signalfd_siginfo`. `ssi_signo` is its
/// first field, a native-endian u32.
pub(crate) fn signo_from_siginfo(siginfo: &[u8; SIGINFO_SIZE]) -> u32 {
    let [a, b, c, d, ..] = *siginfo;
    u32::from_ne_bytes([a, b, c, d])
}

#[cfg(test)]
mod tests;
