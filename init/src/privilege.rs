//! PID 1's privilege drop (init.md, "Privilege"): the order of the steps,
//! what the kernel must show afterwards, and that any failure is fatal.
//! `boot` supplies the system calls.

use crate::machine::Failure;

/// The init's own user and group (`init_uid` and `init_gid` in init.md, and
/// `init` in `images/users.toml`).
pub(crate) const INIT_UID: u32 = 900;
pub(crate) const INIT_GID: u32 = 900;
const _: () = assert!(INIT_UID != 0 && INIT_GID != 0);

const CAP_KILL: u8 = 5;
pub(crate) const CAP_SYS_BOOT: u64 = 1 << 22;

/// What PID 1 keeps, permitted only: `CAP_KILL` to signal a service, and
/// `CAP_SYS_BOOT` for `reboot(2)`. Both are raised to effective around their
/// one call.
pub(crate) const KEPT: u64 = 1 << CAP_KILL | CAP_SYS_BOOT;

/// `NOROOT`, `NO_SETUID_FIXUP` and `NO_CAP_AMBIENT_RAISE`, each with its
/// lock. `KEEP_CAPS` stays clear: with no fixup nothing is cleared on the uid
/// change, so there is nothing to keep. `NO_CAP_AMBIENT_RAISE` is there
/// because PID 1 has no seccomp filter yet: without it a compromised PID 1
/// could raise `CAP_KILL` or `CAP_SYS_BOOT` into the ambient set and hand it
/// to whatever it executes.
pub(crate) const SECUREBITS: u32 = 0b1100_1111;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Step {
    /// Set and lock [`SECUREBITS`]: nothing PID 1 starts gains capabilities
    /// from uid 0, the uid change leaves the sets alone, and the ambient set
    /// cannot be raised.
    Securebits,
    /// Remove everything but [`KEPT`] from the bounding set.
    BoundingSet,
    /// Empty the ambient set.
    Ambient,
    Groups,
    Gid,
    Uid,
    /// Permitted [`KEPT`], effective and inheritable empty.
    Capabilities,
    /// `PR_SET_DUMPABLE` 0: no ptrace, no memory through `/proc`.
    Dumpable,
    NoNewPrivs,
}

impl Step {
    pub(crate) fn name(self) -> &'static str {
        match self {
            Step::Securebits => "drop: securebits",
            Step::BoundingSet => "drop: bounding set",
            Step::Ambient => "drop: ambient set",
            Step::Groups => "drop: setgroups",
            Step::Gid => "drop: setresgid",
            Step::Uid => "drop: setresuid",
            Step::Capabilities => "drop: capset",
            Step::Dumpable => "drop: dumpable",
            Step::NoNewPrivs => "drop: no_new_privs",
        }
    }
}

/// The order matters: the securebits and the bounding set need
/// `CAP_SETPCAP`, the id changes need `CAP_SETUID` and `CAP_SETGID`, and
/// `capset` narrows last of the three because every id change before it
/// would otherwise have cleared the sets.
pub(crate) const STEPS: [Step; 9] = [
    Step::Securebits,
    Step::BoundingSet,
    Step::Ambient,
    Step::Groups,
    Step::Gid,
    Step::Uid,
    Step::Capabilities,
    Step::Dumpable,
    Step::NoNewPrivs,
];

/// What the kernel reports for the calling thread, read back after the
/// drop.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct State {
    /// Real, effective and saved.
    pub(crate) uid: [u32; 3],
    pub(crate) gid: [u32; 3],
    pub(crate) groups: usize,
    pub(crate) permitted: u64,
    pub(crate) effective: u64,
    pub(crate) inheritable: u64,
    pub(crate) ambient: u64,
    pub(crate) bounding: u64,
    pub(crate) securebits: u32,
    pub(crate) no_new_privs: bool,
    pub(crate) dumpable: bool,
    /// The filesystem ids, which `setresuid` and `setresgid` move with the
    /// effective ones.
    pub(crate) fsuid: u32,
    pub(crate) fsgid: u32,
}

pub(crate) trait PrivilegeOps {
    fn apply(&mut self, step: Step) -> Result<(), i32>;
    fn read_back(&mut self) -> Result<State, i32>;
}

/// What is wrong with `state`, if anything, as the step name the failure
/// record carries. The drop is not trusted because every call returned
/// success: the machine must not run until the kernel says PID 1 is
/// unprivileged.
pub(crate) fn check(state: &State) -> Result<(), &'static str> {
    if state.uid != [INIT_UID; 3] {
        return Err("drop: verify uid");
    }
    if state.gid != [INIT_GID; 3] {
        return Err("drop: verify gid");
    }
    if state.fsuid != INIT_UID || state.fsgid != INIT_GID {
        return Err("drop: verify fsuid");
    }
    if state.groups != 0 {
        return Err("drop: verify groups");
    }
    if state.permitted != KEPT {
        return Err("drop: verify permitted");
    }
    if state.effective != 0 {
        return Err("drop: verify effective");
    }
    if state.inheritable != 0 {
        return Err("drop: verify inheritable");
    }
    if state.ambient != 0 {
        return Err("drop: verify ambient");
    }
    if state.bounding != KEPT {
        return Err("drop: verify bounding");
    }
    if state.securebits != SECUREBITS {
        return Err("drop: verify securebits");
    }
    if !state.no_new_privs {
        return Err("drop: verify no_new_privs");
    }
    if state.dumpable {
        return Err("drop: verify dumpable");
    }
    Ok(())
}

/// Drops root. The first failing step, or a state the kernel does not
/// confirm, is returned and the caller must power off: a machine with a root
/// PID 1 must not run (S24, I18).
pub(crate) fn drop_root(ops: &mut impl PrivilegeOps) -> Result<State, Failure> {
    for step in STEPS {
        ops.apply(step).map_err(|errno| Failure {
            step: step.name(),
            errno,
        })?;
    }
    let state = ops.read_back().map_err(|errno| Failure {
        step: "drop: read back",
        errno,
    })?;
    // EPERM: the kernel disagrees with what was asked for.
    check(&state).map_err(|step| Failure { step, errno: 1 })?;
    Ok(state)
}

#[cfg(all(target_os = "linux", not(test)))]
pub(crate) mod linux;

#[cfg(test)]
mod tests;
