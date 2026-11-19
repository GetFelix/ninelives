// Test arithmetic on small, known values.
#![allow(clippy::arithmetic_side_effects)]

use super::*;
use crate::signals::{SIGCHLD, SIGINT, SIGTERM, SIGUSR2};

const SIGHUP: u32 = 1;
const T0: Duration = Duration::from_secs(5);

fn secs(s: u64) -> Duration {
    T0 + Duration::from_secs(s)
}

fn up() -> Machine {
    Machine::start(BootOutcome::Up, T0).0
}

fn records(actions: &[Action]) -> Vec<Record> {
    actions
        .iter()
        .filter_map(|a| match a {
            Action::Record(r) => Some(*r),
            _ => None,
        })
        .collect()
}

fn reboots(actions: &[Action]) -> Vec<StopKind> {
    actions
        .iter()
        .filter_map(|a| match a {
            Action::Reboot(k) => Some(*k),
            _ => None,
        })
        .collect()
}

const FAILURE: Failure = Failure {
    step: "mount /proc",
    errno: 2,
};

#[test]
fn a_clean_boot_reaps_then_reports_up() {
    let (machine, actions) = Machine::start(BootOutcome::Up, T0);
    assert_eq!(
        actions,
        vec![Action::Reap, Action::Record(Record::new(MachineState::Up))]
    );
    assert_eq!(machine.state(), MachineState::Up);
    assert_eq!(machine.next_deadline(), None);
}

/// I2: a SIGCHLD always yields a reap, in every state.
#[test]
fn i2_sigchld_reaps_in_every_state() {
    let mut machines = vec![
        up(),
        Machine::start(BootOutcome::Emergency(FAILURE), T0).0,
        Machine::start(BootOutcome::PowerOff(FAILURE), T0).0,
    ];
    let mut rebooting = up();
    rebooting.handle(T0, Event::Signal(SIGTERM));
    rebooting.handle(T0, Event::SyncDone);
    machines.push(rebooting);
    for mut m in machines {
        let state = m.state();
        assert_eq!(m.handle(T0, Event::Signal(SIGCHLD)), vec![Action::Reap]);
        assert_eq!(m.state(), state);
    }
}

#[test]
fn reaped_children_are_counted_in_the_final_record() {
    let mut m = up();
    m.handle(T0, Event::Reaped(2));
    m.handle(T0, Event::Reaped(3));
    m.handle(T0, Event::Signal(SIGUSR2));
    let actions = m.handle(secs(1), Event::SyncDone);
    let last = records(&actions).pop().unwrap();
    assert_eq!(last.orphans_reaped, Some(5));
}

#[test]
fn each_stop_signal_syncs_then_takes_its_action() {
    for (signo, kind) in [
        (SIGTERM, StopKind::Reboot),
        (SIGINT, StopKind::Reboot),
        (SIGUSR2, StopKind::PowerOff),
    ] {
        let mut m = up();
        let actions = m.handle(T0, Event::Signal(signo));
        assert_eq!(
            actions,
            vec![
                Action::Record(Record {
                    signal: Some(signo),
                    action: Some(kind.as_str()),
                    ..Record::new(MachineState::Stopping)
                }),
                Action::StartSync,
            ]
        );
        assert_eq!(m.state(), MachineState::Stopping);
        assert_eq!(m.next_deadline(), Some(T0 + SYNC_BOUND));

        let actions = m.handle(secs(1), Event::SyncDone);
        let recs = records(&actions);
        assert_eq!(recs[0].step, Some("sync"));
        assert_eq!(recs[0].result, Some("done"));
        assert_eq!(recs[1].step, Some("reboot"));
        assert_eq!(recs[1].action, Some(kind.as_str()));
        // The final record is written before reboot(2) is asked for.
        assert_eq!(actions.last(), Some(&Action::Reboot(kind)));
    }
}

/// I10, with the exception it allows: a bound that passed is recorded first.
#[test]
fn i10_a_hung_sync_is_recorded_then_reboots_anyway() {
    let mut m = up();
    m.handle(T0, Event::Signal(SIGUSR2));
    assert!(m.handle(secs(29), Event::Tick).is_empty());
    let actions = m.handle(secs(30), Event::Tick);
    let recs = records(&actions);
    assert_eq!(recs[0].result, Some("bound passed"));
    assert_eq!(reboots(&actions), vec![StopKind::PowerOff]);
    // A sync that finishes late does not reboot a second time.
    assert!(reboots(&m.handle(secs(31), Event::SyncDone)).is_empty());
}

#[test]
fn a_request_during_shutdown_only_turns_reboot_into_power_off() {
    let mut m = up();
    m.handle(T0, Event::Signal(SIGTERM));
    let actions = m.handle(T0, Event::Signal(SIGUSR2));
    assert_eq!(records(&actions)[0].action, Some("poweroff"));
    // A later reboot request does not undo it, and starts nothing new.
    let actions = m.handle(T0, Event::Signal(SIGTERM));
    assert_eq!(records(&actions)[0].action, Some("poweroff"));
    assert!(!actions.contains(&Action::StartSync));
    let actions = m.handle(secs(1), Event::SyncDone);
    assert_eq!(reboots(&actions), vec![StopKind::PowerOff]);
}

#[test]
fn a_request_while_rebooting_changes_the_retry() {
    let mut m = up();
    m.handle(T0, Event::Signal(SIGTERM));
    m.handle(T0, Event::SyncDone);
    m.handle(T0, Event::RebootFailed(1));
    m.handle(T0, Event::Signal(SIGUSR2));
    let actions = m.handle(T0 + REBOOT_RETRY, Event::Tick);
    assert_eq!(reboots(&actions), vec![StopKind::PowerOff]);
}

#[test]
fn other_signals_are_recorded_and_change_nothing() {
    let mut m = up();
    for signo in [SIGHUP, 3, 6, 10, 11, 30, 34, 64] {
        let actions = m.handle(T0, Event::Signal(signo));
        assert_eq!(
            actions,
            vec![Action::Record(Record {
                signal: Some(signo),
                action: Some("ignored"),
                ..Record::new(MachineState::Up)
            })]
        );
        assert_eq!(m.state(), MachineState::Up);
    }
}

#[test]
fn an_emergency_repeats_its_reason_and_still_stops() {
    let (mut m, actions) = Machine::start(BootOutcome::Emergency(FAILURE), T0);
    let first = records(&actions)[0];
    assert_eq!(first.machine, MachineState::Emergency);
    assert_eq!(first.step, Some("mount /proc"));
    assert_eq!(first.error, Some(2));
    assert!(!actions.iter().any(|a| matches!(a, Action::Reboot(_))));

    assert!(m.handle(secs(29), Event::Tick).is_empty());
    assert_eq!(records(&m.handle(secs(30), Event::Tick)), vec![first]);
    assert_eq!(m.next_deadline(), Some(secs(60)));

    let actions = m.handle(secs(31), Event::Signal(SIGUSR2));
    assert!(actions.contains(&Action::StartSync));
    let actions = m.handle(secs(32), Event::SyncDone);
    assert_eq!(reboots(&actions), vec![StopKind::PowerOff]);
}

#[test]
fn a_loop_that_cannot_hear_requests_powers_off() {
    let (mut m, actions) = Machine::start(BootOutcome::PowerOff(FAILURE), T0);
    assert_eq!(records(&actions)[0].machine, MachineState::Emergency);
    assert_eq!(records(&actions)[1].action, Some("poweroff"));
    assert!(actions.contains(&Action::StartSync));
    let actions = m.handle(T0 + SYNC_BOUND, Event::Tick);
    assert_eq!(reboots(&actions), vec![StopKind::PowerOff]);
}

/// I1: a failed reboot(2) is recorded and tried again; nothing gives up.
#[test]
fn i1_a_failed_reboot_is_retried() {
    let mut m = up();
    m.handle(T0, Event::Signal(SIGUSR2));
    m.handle(T0, Event::SyncDone);
    let actions = m.handle(T0, Event::RebootFailed(1));
    assert_eq!(records(&actions)[0].error, Some(1));
    assert_eq!(m.next_deadline(), Some(T0 + REBOOT_RETRY));
    assert!(
        m.handle(T0 + REBOOT_RETRY - Duration::from_millis(1), Event::Tick)
            .is_empty()
    );
    let actions = m.handle(T0 + REBOOT_RETRY, Event::Tick);
    assert_eq!(reboots(&actions), vec![StopKind::PowerOff]);
}

struct Rng(u64);

impl Rng {
    fn next(&mut self) -> u64 {
        // xorshift64: deterministic, so a failure reproduces from its seed.
        self.0 ^= self.0 << 13;
        self.0 ^= self.0 >> 7;
        self.0 ^= self.0 << 17;
        self.0
    }

    fn event(&mut self) -> Event {
        match self.next() % 7 {
            0 => Event::Signal((self.next() % 65) as u32),
            1 => Event::Signal([SIGTERM, SIGINT, SIGUSR2][(self.next() % 3) as usize]),
            2 => Event::Signal(SIGCHLD),
            3 => Event::Reaped(self.next() % 4),
            4 => Event::SyncDone,
            5 => Event::RebootFailed((self.next() % 40) as i32),
            _ => Event::Tick,
        }
    }
}

/// Random event sequences from every boot outcome, checking after each event:
/// - I1: once stopping, the machine always has a deadline to wake for, so a
///   failed step can never leave it wedged;
/// - I2: every SIGCHLD yields a reap;
/// - I10: reboot(2) is asked for only after the sync started and then either
///   finished or ran out its bound, which is recorded first.
#[test]
fn i1_i2_i10_hold_over_random_event_sequences() {
    for seed in 1..=500u64 {
        let mut rng = Rng(seed.wrapping_mul(0x9e37_79b9_7f4a_7c15));
        let outcome = match seed % 3 {
            0 => BootOutcome::Up,
            1 => BootOutcome::Emergency(FAILURE),
            _ => BootOutcome::PowerOff(FAILURE),
        };
        let mut now = T0;
        let (mut m, first) = Machine::start(outcome, now);
        let mut sync_started_at = first.contains(&Action::StartSync).then_some(now);
        let mut sync_done = false;
        for _ in 0..200 {
            now += Duration::from_millis(rng.next() % 5000);
            let event = rng.event();
            let actions = m.handle(now, event);
            if event == Event::Signal(SIGCHLD) {
                assert!(actions.contains(&Action::Reap), "seed {seed}: I2");
            }
            if actions.contains(&Action::StartSync) {
                assert!(sync_started_at.is_none(), "seed {seed}: one sync per stop");
                sync_started_at = Some(now);
            }
            if event == Event::SyncDone && sync_started_at.is_some() {
                sync_done = true;
            }
            if let Some(i) = actions.iter().position(|a| matches!(a, Action::Reboot(_))) {
                let started = sync_started_at.expect("reboot without a sync");
                let bound_passed = now >= started + SYNC_BOUND;
                assert!(sync_done || bound_passed, "seed {seed}: I10");
                if !sync_done {
                    assert!(
                        records(&actions[..i])
                            .iter()
                            .any(|r| r.result == Some("bound passed")),
                        "seed {seed}: bound not recorded"
                    );
                }
            }
            if actions.iter().any(|a| matches!(a, Action::Reboot(_))) {
                // reboot(2) returning means it failed; the loop says so at once.
                m.handle(now, Event::RebootFailed(1));
            }
            if m.state() == MachineState::Stopping {
                assert!(
                    m.next_deadline().is_some(),
                    "seed {seed}: I1, stopping with nothing to wake for"
                );
            }
        }
    }
}
