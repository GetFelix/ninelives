//! Boot, as root (init.md): the system calls behind `boot_plan`, which
//! holds the order and the fail-closed rules.

use std::ffi::CString;
use std::os::fd::OwnedFd;

use rustix::fs::{Mode, OFlags, open};
use rustix::io::{Errno, read};
use rustix::mount::{MountFlags, mount, mount_bind, mount_remount};
use rustix::system::{RebootCommand, reboot};

use crate::boot_plan::{self, BootOps};
use crate::console::{self, Console};
use crate::machine::{BootOutcome, Failure};
use crate::mounts::{self, Flags, Mount};
use crate::privilege::{self, linux::Linux as Privilege};
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

    let mut ops = Linux {
        console: Console::absent(),
    };
    let emergency = boot_plan::run(&mut ops);

    // Step 11. Before the worker thread starts, so it inherits the dropped
    // state. The drop happens in an emergency too: no root PID 1, ever (S24).
    let dropped = privilege::drop_root(&mut Privilege);
    if let Ok(state) = &dropped {
        ops.console.write(&Record {
            step: Some("drop root"),
            result: Some("ok"),
            privilege: Some(*state),
            ..Record::new(MachineState::Booting)
        });
    }

    let signals = match mask {
        Ok(set) => sys::signalfd(&set).map_err(|e| failure("signalfd", &e)),
        Err(e) => Err(failure("block signals", &e)),
    };
    // A failed drop is fatal and wins over everything else: nothing runs
    // while PID 1 may still be root (I18).
    let (signals, outcome) = match (dropped, signals, emergency) {
        (Err(f), fd, _) => (fd.ok(), BootOutcome::PowerOff(f)),
        (Ok(_), Err(f), _) => (None, BootOutcome::PowerOff(f)),
        (Ok(_), Ok(fd), Some(f)) => (Some(fd), BootOutcome::Emergency(f)),
        (Ok(_), Ok(fd), None) => (Some(fd), BootOutcome::Up),
    };
    Booted {
        console: ops.console,
        signals,
        outcome,
    }
}

struct Linux {
    console: Console,
}

impl BootOps for Linux {
    fn mount(&mut self, m: &Mount) -> Result<(), i32> {
        let data = CString::new(m.data).map_err(|_| Errno::INVAL.raw_os_error())?;
        let data = (!m.data.is_empty()).then_some(data.as_c_str());
        mount(m.fstype, m.target, m.fstype, mount_flags(m.flags), data)
            .map_err(|e| e.raw_os_error())
    }

    fn open_console(&mut self) {
        self.console = Console::open(console::fill_stdio());
    }

    fn read_boot_id(&mut self) {
        self.console.set_boot_id(read_boot_id());
    }

    fn lock_proc_sys(&mut self) -> Result<(), i32> {
        mount_bind(mounts::PROC_SYS, mounts::PROC_SYS)
            .and_then(|()| {
                mount_remount(
                    mounts::PROC_SYS,
                    MountFlags::BIND | MountFlags::RDONLY | mount_flags(mounts::PROC_SYS_FLAGS),
                    "",
                )
            })
            .map_err(|e| e.raw_os_error())
    }

    fn ctrl_alt_del_off(&mut self) -> Result<(), i32> {
        reboot(RebootCommand::CadOff).map_err(|e| e.raw_os_error())
    }

    fn record(&mut self, record: &Record) {
        self.console.write(record);
    }
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

fn failure(step: &'static str, e: &std::io::Error) -> Failure {
    Failure {
        step,
        errno: e.raw_os_error().unwrap_or(0),
    }
}
