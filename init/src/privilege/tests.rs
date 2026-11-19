use super::*;

const GOOD: State = State {
    uid: [INIT_UID; 3],
    gid: [INIT_GID; 3],
    groups: 0,
    permitted: KEPT,
    effective: 0,
    inheritable: 0,
    ambient: 0,
    bounding: KEPT,
    securebits: SECUREBITS,
    no_new_privs: true,
    dumpable: false,
    fsuid: INIT_UID,
    fsgid: INIT_GID,
};

struct Fake {
    applied: Vec<Step>,
    fail: Option<Step>,
    state: Result<State, i32>,
}

impl Fake {
    fn new() -> Fake {
        Fake {
            applied: Vec::new(),
            fail: None,
            state: Ok(GOOD),
        }
    }
}

impl PrivilegeOps for Fake {
    fn apply(&mut self, step: Step) -> Result<(), i32> {
        self.applied.push(step);
        if self.fail == Some(step) {
            Err(1)
        } else {
            Ok(())
        }
    }
    fn read_back(&mut self) -> Result<State, i32> {
        self.state
    }
}

fn index(step: Step) -> usize {
    STEPS.iter().position(|s| *s == step).unwrap()
}

#[test]
fn the_steps_run_in_the_designed_order() {
    let mut fake = Fake::new();
    assert_eq!(drop_root(&mut fake), Ok(GOOD));
    assert_eq!(fake.applied, STEPS);
}

/// The securebits and the bounding set need capabilities the id change and
/// the capset take away; the ids must change before the capset narrows.
#[test]
fn the_order_keeps_what_each_step_needs() {
    assert_eq!(index(Step::Securebits), 0);
    assert!(index(Step::Securebits) < index(Step::Uid));
    assert!(index(Step::BoundingSet) < index(Step::Capabilities));
    assert!(index(Step::Groups) < index(Step::Gid));
    assert!(index(Step::Gid) < index(Step::Uid));
    assert!(index(Step::Uid) < index(Step::Capabilities));
    assert!(index(Step::Capabilities) < index(Step::NoNewPrivs));
}

#[test]
fn what_pid1_keeps_is_kill_and_sys_boot_only() {
    assert_eq!(KEPT, (1 << 5) | (1 << 22));
    assert_eq!(KEPT.count_ones(), 2);
}

#[test]
fn securebits_lock_both_bits() {
    // NOROOT, NO_SETUID_FIXUP and NO_CAP_AMBIENT_RAISE, each with its lock.
    assert_eq!(SECUREBITS, 0xcf);
}

/// I18: any failing step is fatal, and nothing after it runs.
#[test]
fn every_failing_step_is_fatal_and_stops_the_drop() {
    for (i, step) in STEPS.into_iter().enumerate() {
        let mut fake = Fake::new();
        fake.fail = Some(step);
        let failure = drop_root(&mut fake).unwrap_err();
        assert_eq!(failure.step, step.name());
        assert_eq!(failure.errno, 1);
        assert_eq!(fake.applied.len(), i + 1, "{step:?}");
    }
}

#[test]
fn a_failed_read_back_is_fatal() {
    let mut fake = Fake::new();
    fake.state = Err(13);
    let failure = drop_root(&mut fake).unwrap_err();
    assert_eq!((failure.step, failure.errno), ("drop: read back", 13));
}

/// Every field the kernel could leave wrong makes the drop fail, even
/// though every call reported success.
#[test]
fn a_state_the_kernel_does_not_confirm_is_fatal() {
    type Mutation = fn(&mut State);
    let bad: [(&str, Mutation); 19] = [
        ("uid", |s| s.uid = [0, INIT_UID, INIT_UID]),
        ("uid", |s| s.uid = [INIT_UID, 0, INIT_UID]),
        ("uid", |s| s.uid = [INIT_UID, INIT_UID, 0]),
        ("gid", |s| s.gid = [0, INIT_GID, INIT_GID]),
        ("gid", |s| s.gid = [INIT_GID, INIT_GID, 0]),
        ("fsuid", |s| s.fsuid = 0),
        ("fsuid", |s| s.fsgid = 0),
        ("groups", |s| s.groups = 1),
        ("permitted", |s| s.permitted = KEPT | 1 << 21),
        ("permitted", |s| s.permitted = 1 << 5),
        ("effective", |s| s.effective = 1 << 5),
        ("inheritable", |s| s.inheritable = 1 << 5),
        ("ambient", |s| s.ambient = 1 << 5),
        ("bounding", |s| s.bounding = u64::MAX),
        ("securebits", |s| s.securebits = 0b0101),
        // A superset is not the exact value either.
        ("securebits", |s| s.securebits = SECUREBITS | 1 << 4),
        ("securebits", |s| s.securebits = 0b0000_1111),
        ("no_new_privs", |s| s.no_new_privs = false),
        ("dumpable", |s| s.dumpable = true),
    ];
    for (i, (field, mutate)) in bad.into_iter().enumerate() {
        let mut state = GOOD;
        mutate(&mut state);
        assert!(check(&state).is_err(), "case {i}");
        let mut fake = Fake::new();
        fake.state = Ok(state);
        let failure = drop_root(&mut fake).unwrap_err();
        // The console names the field that was wrong.
        assert!(failure.step.starts_with("drop: verify"), "case {i}");
        assert!(failure.step.contains(field), "case {i}: {}", failure.step);
    }
}

#[test]
fn a_root_pid1_is_never_accepted() {
    let mut state = GOOD;
    state.uid = [0; 3];
    state.gid = [0; 3];
    assert!(check(&state).is_err());
}
