//! Boot, as root (init.md): the steps of that section this slice performs,
//! in order. Everything else in it is marked where it will go.

use std::ffi::CString;
use std::os::fd::OwnedFd;

use rustix::fs::{Mode, OFlags, open};
use rustix::io::{Errno, read};
use rustix::mount::{MountFlags, mount, mount_bind, mount_remount};
use rustix::system::{RebootCommand, reboot};

use crate::console::{self, Console};
use crate::machine::{BootOutcome, Failure};
use crate::mounts::{self, Flags, Mount};
use crate::record::{self, MachineState, Record};
use crate::sys;

pub(crate) struct Booted {
    pub(crate) console: Console,
    pub(crate) signals: Option<OwnedFd>,
    pub(crate) outcome: BootOutcome,
}

pub(crate) fn run() -> Booted {
    // First, so a stop request sent during boot waits in the signalfd. The
    // kernel discards a signal sent to PID 1 that has its default action.
    let mask = sys::block_all_signals();

    let mut console = Console::absent();
    let mut emergency = None;
    // Step 1, failing closed: the first failure ends the mounts.
    for m in &mounts::EARLY {
        if let Err(e) = mount_one(m) {
            emergency = Some(failure(m.step, e));
            break;
        }
        match m.target {
            "/dev" => console = Console::open(console::fill_stdio()),
            "/proc" => {
                console.set_boot_id(read_boot_id());
                console.write(&Record::new(MachineState::Booting));
            }
            _ => {}
        }
    }
    // TODO(#48): steps 2 to 6: the config and its [[mount]] entries, the data
    // partition (#4), the directories and the cgroup tree.

    // Step 7, the part this slice needs.
    if emergency.is_none()
        && let Err(e) = lock_proc_sys()
    {
        // The lockdown is what keeps every later process from changing a
        // sysctl (I19), so failing it is failing closed.
        emergency = Some(failure("remount /proc/sys", e));
    }
    // Without this Ctrl-Alt-Del reboots at once, with no sync. Losing it
    // costs only that, so it is recorded and boot goes on.
    if let Err(e) = reboot(RebootCommand::CadOff) {
        console.write(&Record {
            step: Some("ctrl-alt-del off"),
            error: Some(e.raw_os_error()),
            ..Record::new(MachineState::Booting)
        });
    }
    // TODO(#48): the rest of step 7 (host name, loopback, rlimits), step 8
    // (watchdog, /dev/kmsg reader, power button: #86) and steps 9 and 10.
    // TODO(#68): step 11, the privilege drop. Until then PID 1 stays root.

    let signals = match mask {
        Ok(set) => sys::signalfd(&set).map_err(|e| failure("signalfd", errno_of(&e))),
        Err(e) => Err(failure("block signals", errno_of(&e))),
    };
    let (signals, outcome) = match (signals, emergency) {
        (Err(f), _) => (None, BootOutcome::PowerOff(f)),
        (Ok(fd), Some(f)) => (Some(fd), BootOutcome::Emergency(f)),
        (Ok(fd), None) => (Some(fd), BootOutcome::Up),
    };
    Booted {
        console,
        signals,
        outcome,
    }
}

fn mount_one(m: &Mount) -> Result<(), Errno> {
    let data = CString::new(m.data).map_err(|_| Errno::INVAL)?;
    let data = (!m.data.is_empty()).then_some(data.as_c_str());
    mount(m.fstype, m.target, m.fstype, mount_flags(m.flags), data)
}

fn lock_proc_sys() -> Result<(), Errno> {
    mount_bind(mounts::PROC_SYS, mounts::PROC_SYS)?;
    mount_remount(
        mounts::PROC_SYS,
        MountFlags::BIND | MountFlags::RDONLY | mount_flags(mounts::PROC_SYS_FLAGS),
        "",
    )
}

fn mount_flags(f: Flags) -> MountFlags {
    let mut flags = MountFlags::empty();
    flags.set(MountFlags::NOSUID, f.nosuid);
    flags.set(MountFlags::NODEV, f.nodev);
    flags.set(MountFlags::NOEXEC, f.noexec);
    flags
}

/// The kernel's id for this boot, which every record carries.
fn read_boot_id() -> Option<String> {
    let fd = open(
        "/proc/sys/kernel/random/boot_id",
        OFlags::RDONLY | OFlags::CLOEXEC,
        Mode::empty(),
    )
    .ok()?;
    let mut buf = [0u8; 64];
    let n = read(&fd, &mut buf).ok()?;
    record::parse_boot_id(buf.get(..n)?).map(str::to_owned)
}

fn failure(step: &'static str, e: Errno) -> Failure {
    Failure {
        step,
        errno: e.raw_os_error(),
    }
}

fn errno_of(e: &std::io::Error) -> Errno {
    Errno::from_raw_os_error(e.raw_os_error().unwrap_or(0))
}
