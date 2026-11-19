//! The order of the boot steps this slice performs and what a failure stops
//! (init.md, "Boot, as root", steps 1 and 7). `boot` supplies the system
//! calls; the order and the fail-closed rules live here so they are tested.

use crate::machine::{Failure, emergency_record};
use crate::mounts::{self, Mount};
use crate::record::{MachineState, Record};

pub(crate) trait BootOps {
    fn mount(&mut self, m: &Mount) -> Result<(), i32>;
    /// Called once, right after the `/dev` attempt.
    fn open_console(&mut self);
    fn read_boot_id(&mut self);
    fn lock_proc_sys(&mut self) -> Result<(), i32>;
    fn ctrl_alt_del_off(&mut self) -> Result<(), i32>;
    fn record(&mut self, record: &Record);
}

/// Runs the steps and returns the failure that makes the machine
/// `emergency`, if any.
pub(crate) fn run(ops: &mut impl BootOps) -> Option<Failure> {
    let mut emergency = None;
    let mut proc_mounted = false;
    // Step 1, failing closed: the first failure ends the mounts.
    for m in &mounts::EARLY {
        let mounted = ops.mount(m);
        if m.target == "/dev" {
            // Even without /dev, fd 1 may be a console the kernel opened, and
            // a failure has to be reported somewhere.
            ops.open_console();
        }
        if let Err(errno) = mounted {
            emergency = Some(Failure {
                step: m.step,
                errno,
            });
            break;
        }
        if m.target == "/proc" {
            proc_mounted = true;
            ops.read_boot_id();
            ops.record(&Record::new(MachineState::Booting));
        }
    }
    // TODO(#48): steps 2 to 6: the config and its [[mount]] entries, the data
    // partition (#4), the directories and the cgroup tree.

    // Step 7, the part this slice needs. /proc/sys is locked whenever /proc
    // is mounted, whatever failed after it: the lockdown is what keeps every
    // later process from changing a sysctl (I19).
    if proc_mounted && let Err(errno) = ops.lock_proc_sys() {
        let failure = Failure {
            step: "remount /proc/sys",
            errno,
        };
        match emergency {
            None => emergency = Some(failure),
            Some(_) => ops.record(&emergency_record(failure)),
        }
    }
    // Without this Ctrl-Alt-Del reboots at once, with no sync. Losing it
    // costs only that, so it is recorded and boot goes on.
    if let Err(errno) = ops.ctrl_alt_del_off() {
        ops.record(&Record {
            step: Some("ctrl-alt-del off"),
            error: Some(errno),
            ..Record::new(MachineState::Booting)
        });
    }
    // TODO(#48): the rest of step 7 (host name, loopback, rlimits), step 8
    // (watchdog, /dev/kmsg reader, power button: #86) and steps 9 and 10.
    // Step 11, the privilege drop, is `privilege::drop_root`, which `boot::run`
    // calls next.
    emergency
}

#[cfg(test)]
mod tests;
