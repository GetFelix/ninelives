//! The system calls behind [`super::drop_root`].

use rustix::io::Errno;
use rustix::process::{
    DumpableBehavior, Gid, Uid, dumpable_behavior, getgroups, set_dumpable_behavior,
};
use rustix::thread::{
    CapabilitiesSecureBits, CapabilitySet, CapabilitySets, capabilities, capabilities_secure_bits,
    capability_is_in_ambient_set, capability_is_in_bounding_set, clear_ambient_capability_set,
    no_new_privs, remove_capability_from_bounding_set, set_capabilities,
    set_capabilities_secure_bits, set_no_new_privs, set_thread_groups, set_thread_res_gid,
    set_thread_res_uid,
};

use super::{INIT_GID, INIT_UID, KEPT, PrivilegeOps, SECUREBITS, State, Step};
use crate::sys;

/// Everything here acts on the calling thread, so the drop runs before the
/// init starts any other: threads inherit the dropped state from their
/// creator.
pub(crate) struct Linux;

impl PrivilegeOps for Linux {
    fn apply(&mut self, step: Step) -> Result<(), i32> {
        apply(step).map_err(|e| e.raw_os_error())
    }

    fn read_back(&mut self) -> Result<State, i32> {
        read_back().map_err(|e| e.raw_os_error())
    }
}

fn apply(step: Step) -> Result<(), Errno> {
    match step {
        Step::Securebits => {
            set_capabilities_secure_bits(CapabilitiesSecureBits::from_bits_retain(SECUREBITS))
        }
        Step::BoundingSet => {
            for cap in every_capability() {
                if cap.bits() & KEPT == 0 && in_set(capability_is_in_bounding_set(cap))? {
                    remove_capability_from_bounding_set(cap)?;
                }
            }
            Ok(())
        }
        Step::Ambient => clear_ambient_capability_set(),
        Step::Groups => set_thread_groups(&[]),
        Step::Gid => {
            let gid = Gid::from_raw(INIT_GID);
            set_thread_res_gid(gid, gid, gid)
        }
        Step::Uid => {
            let uid = Uid::from_raw(INIT_UID);
            set_thread_res_uid(uid, uid, uid)
        }
        Step::Capabilities => set_capabilities(
            None,
            CapabilitySets {
                effective: CapabilitySet::empty(),
                permitted: CapabilitySet::from_bits_retain(KEPT),
                inheritable: CapabilitySet::empty(),
            },
        ),
        Step::Dumpable => set_dumpable_behavior(DumpableBehavior::NotDumpable),
        Step::NoNewPrivs => set_no_new_privs(true),
    }
}

fn read_back() -> Result<State, Errno> {
    let caps = capabilities(None)?;
    let mut bounding = 0u64;
    let mut ambient = 0u64;
    for cap in every_capability() {
        if in_set(capability_is_in_bounding_set(cap))? {
            bounding |= cap.bits();
        }
        if in_set(capability_is_in_ambient_set(cap))? {
            ambient |= cap.bits();
        }
    }
    let io = |e: std::io::Error| Errno::from_io_error(&e).unwrap_or(Errno::IO);
    Ok(State {
        uid: sys::getresuid().map_err(io)?,
        gid: sys::getresgid().map_err(io)?,
        groups: getgroups()?.len(),
        permitted: caps.permitted.bits(),
        effective: caps.effective.bits(),
        inheritable: caps.inheritable.bits(),
        ambient,
        bounding,
        securebits: capabilities_secure_bits()?.bits(),
        no_new_privs: no_new_privs()?,
        dumpable: dumpable_behavior()? != DumpableBehavior::NotDumpable,
        fsuid: sys::fsuid(),
        fsgid: sys::fsgid(),
    })
}

/// Every capability number a 64-bit set can hold. The kernel answers `EINVAL`
/// for the ones past the last it knows.
fn every_capability() -> impl Iterator<Item = CapabilitySet> {
    (0..u64::BITS).map(|n| CapabilitySet::from_bits_retain(1 << n))
}

/// A capability the running kernel does not have is in no set.
fn in_set(answer: Result<bool, Errno>) -> Result<bool, Errno> {
    match answer {
        Err(Errno::INVAL) => Ok(false),
        other => other,
    }
}

/// Why [`raised`] failed.
pub(crate) enum Raised {
    /// The capability could not be raised; `call` did not run.
    Raise(i32),
    /// `call` ran and returned, but the capability could not be lowered
    /// again, so PID 1 still holds it effective. The caller must not go on.
    Lower,
}

/// Runs `call` with `caps` raised to effective, then lowers them. PID 1
/// holds its two capabilities permitted only (I17).
pub(crate) fn raised<T>(caps: u64, call: impl FnOnce() -> T) -> Result<T, Raised> {
    let held = capabilities(None).map_err(|e| Raised::Raise(e.raw_os_error()))?;
    let with = CapabilitySets {
        effective: held.effective | CapabilitySet::from_bits_retain(caps),
        ..held
    };
    set_capabilities(None, with).map_err(|e| Raised::Raise(e.raw_os_error()))?;
    let result = call();
    set_capabilities(None, held).map_err(|_| Raised::Lower)?;
    Ok(result)
}
