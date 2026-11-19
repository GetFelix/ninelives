use super::*;

#[derive(Clone, Debug, PartialEq)]
enum Call {
    Mount(&'static str),
    OpenConsole,
    ReadBootId,
    LockProcSys,
    CtrlAltDelOff,
    Record(Record),
}

#[derive(Default)]
struct Fake {
    calls: Vec<Call>,
    fail_mount: Option<&'static str>,
    fail_lock: bool,
    fail_cad: bool,
}

impl BootOps for Fake {
    fn mount(&mut self, m: &Mount) -> Result<(), i32> {
        self.calls.push(Call::Mount(m.target));
        if self.fail_mount == Some(m.target) {
            Err(16)
        } else {
            Ok(())
        }
    }
    fn open_console(&mut self) {
        self.calls.push(Call::OpenConsole);
    }
    fn read_boot_id(&mut self) {
        self.calls.push(Call::ReadBootId);
    }
    fn lock_proc_sys(&mut self) -> Result<(), i32> {
        self.calls.push(Call::LockProcSys);
        if self.fail_lock { Err(1) } else { Ok(()) }
    }
    fn ctrl_alt_del_off(&mut self) -> Result<(), i32> {
        self.calls.push(Call::CtrlAltDelOff);
        if self.fail_cad { Err(22) } else { Ok(()) }
    }
    fn record(&mut self, record: &Record) {
        self.calls.push(Call::Record(*record));
    }
}

fn position(calls: &[Call], call: &Call) -> usize {
    calls
        .iter()
        .position(|c| c == call)
        .unwrap_or_else(|| panic!("{call:?} not in {calls:?}"))
}

#[test]
fn a_clean_boot_runs_every_step_in_order() {
    let mut fake = Fake::default();
    assert_eq!(run(&mut fake), None);
    assert_eq!(
        fake.calls,
        vec![
            Call::Mount("/dev"),
            Call::OpenConsole,
            Call::Mount("/proc"),
            Call::ReadBootId,
            Call::Record(Record::new(MachineState::Booting)),
            Call::Mount("/sys"),
            Call::Mount("/sys/fs/cgroup"),
            Call::Mount("/run"),
            Call::Mount("/tmp"),
            Call::LockProcSys,
            Call::CtrlAltDelOff,
        ]
    );
}

#[test]
fn dev_is_mounted_first_so_later_failures_reach_the_console() {
    let mut fake = Fake::default();
    run(&mut fake);
    assert_eq!(fake.calls[0], Call::Mount("/dev"));
    assert_eq!(fake.calls[1], Call::OpenConsole);
}

#[test]
fn cgroup_is_mounted_after_sys() {
    let mut fake = Fake::default();
    run(&mut fake);
    assert!(
        position(&fake.calls, &Call::Mount("/sys"))
            < position(&fake.calls, &Call::Mount("/sys/fs/cgroup"))
    );
}

#[test]
fn a_failed_dev_still_opens_the_console_and_stops_the_mounts() {
    let mut fake = Fake {
        fail_mount: Some("/dev"),
        ..Fake::default()
    };
    let failure = run(&mut fake).unwrap();
    assert_eq!(failure.step, "mount /dev");
    assert_eq!(failure.errno, 16);
    assert_eq!(
        fake.calls,
        vec![Call::Mount("/dev"), Call::OpenConsole, Call::CtrlAltDelOff]
    );
}

/// I19, fail closed: /proc/sys is locked whenever /proc is mounted, even
/// when a later mount failed.
#[test]
fn proc_sys_is_locked_after_any_failure_once_proc_is_mounted() {
    for target in ["/sys", "/sys/fs/cgroup", "/run", "/tmp"] {
        let mut fake = Fake {
            fail_mount: Some(target),
            ..Fake::default()
        };
        let failure = run(&mut fake).unwrap();
        assert_eq!(failure.step, format!("mount {target}"));
        assert!(fake.calls.contains(&Call::LockProcSys), "{target}");
        // The mounts after the failed one are not tried.
        let after = position(&fake.calls, &Call::Mount(target)) + 1;
        assert!(
            !fake.calls[after..]
                .iter()
                .any(|c| matches!(c, Call::Mount(_))),
            "{target}"
        );
    }
}

#[test]
fn proc_sys_is_not_touched_without_proc() {
    let mut fake = Fake {
        fail_mount: Some("/proc"),
        ..Fake::default()
    };
    assert_eq!(run(&mut fake).unwrap().step, "mount /proc");
    assert!(!fake.calls.contains(&Call::LockProcSys));
    assert!(!fake.calls.contains(&Call::ReadBootId));
}

#[test]
fn a_failed_lockdown_is_an_emergency() {
    let mut fake = Fake {
        fail_lock: true,
        ..Fake::default()
    };
    let failure = run(&mut fake).unwrap();
    assert_eq!(failure.step, "remount /proc/sys");
    assert_eq!(failure.errno, 1);
}

#[test]
fn a_failed_lockdown_after_a_failed_mount_is_recorded_too() {
    let mut fake = Fake {
        fail_mount: Some("/run"),
        fail_lock: true,
        ..Fake::default()
    };
    // The first failure is the reason; the second is still on the console.
    assert_eq!(run(&mut fake).unwrap().step, "mount /run");
    assert!(fake.calls.iter().any(|c| matches!(
        c,
        Call::Record(r) if r.machine == MachineState::Emergency
            && r.step == Some("remount /proc/sys")
    )));
}

#[test]
fn a_failed_ctrl_alt_del_off_is_recorded_and_not_an_emergency() {
    let mut fake = Fake {
        fail_cad: true,
        ..Fake::default()
    };
    assert_eq!(run(&mut fake), None);
    assert!(fake.calls.iter().any(|c| matches!(
        c,
        Call::Record(r) if r.step == Some("ctrl-alt-del off") && r.error == Some(22)
    )));
}
