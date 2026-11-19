// Test arithmetic on small, known values.
#![allow(clippy::arithmetic_side_effects)]

use std::collections::VecDeque;

use super::*;
use crate::machine::{BootOutcome, REBOOT_RETRY, SYNC_BOUND};
use crate::record::MachineState;
use crate::signals::{SIGCHLD, SIGUSR2};

#[derive(Clone, Debug, PartialEq)]
enum Call {
    Wait(Option<Duration>),
    Reap,
    Record(Record),
    StartSync,
    Reboot(StopKind),
}

/// Plays back one batch of events per `wait`, advancing the clock by the
/// batch's delay, and logs every call the loop makes.
struct FakeOs {
    now: Duration,
    script: VecDeque<(Duration, Vec<Event>)>,
    calls: Vec<Call>,
    children: u64,
    reboot_errno: i32,
    sync_errno: Option<i32>,
}

impl FakeOs {
    fn new(script: Vec<(u64, Vec<Event>)>) -> FakeOs {
        FakeOs {
            now: Duration::from_secs(1),
            script: script
                .into_iter()
                .map(|(ms, ev)| (Duration::from_millis(ms), ev))
                .collect(),
            calls: Vec::new(),
            children: 0,
            reboot_errno: 1,
            sync_errno: None,
        }
    }
}

impl Os for FakeOs {
    fn now(&self) -> Duration {
        self.now
    }

    fn wait(&mut self, deadline: Option<Duration>, events: &mut Vec<Event>) {
        self.calls.push(Call::Wait(deadline));
        let (delay, batch) = self.script.pop_front().expect("script ran out");
        self.now += delay;
        events.extend(batch);
    }

    fn reap(&mut self) -> u64 {
        self.calls.push(Call::Reap);
        std::mem::take(&mut self.children)
    }

    fn record(&mut self, record: &Record) {
        self.calls.push(Call::Record(*record));
    }

    fn start_sync(&mut self) -> Result<(), i32> {
        self.calls.push(Call::StartSync);
        self.sync_errno.map_or(Ok(()), Err)
    }

    fn reboot(&mut self, kind: StopKind) -> i32 {
        self.calls.push(Call::Reboot(kind));
        self.reboot_errno
    }
}

fn boot(os: &mut FakeOs) -> Machine {
    let (mut machine, first) = Machine::start(BootOutcome::Up, os.now());
    let now = os.now();
    execute_all(os, &mut machine, now, first);
    machine
}

fn run_script(os: &mut FakeOs, machine: &mut Machine) {
    let mut events = Vec::new();
    while !os.script.is_empty() {
        step(os, machine, &mut events);
    }
}

/// I2: the reap a SIGCHLD calls for runs before the loop waits again.
#[test]
fn i2_sigchld_is_reaped_in_the_same_iteration() {
    let mut os = FakeOs::new(vec![(0, vec![Event::Signal(SIGCHLD)]), (0, vec![])]);
    let mut machine = boot(&mut os);
    os.calls.clear();
    os.children = 3;
    run_script(&mut os, &mut machine);
    assert_eq!(os.calls[0], Call::Wait(None));
    assert_eq!(os.calls[1], Call::Reap);
    assert_eq!(os.calls[2], Call::Wait(None));
}

#[test]
fn boot_reaps_before_the_first_wait() {
    let mut os = FakeOs::new(vec![]);
    boot(&mut os);
    assert_eq!(os.calls[0], Call::Reap);
    assert_eq!(os.calls[1], Call::Record(Record::new(MachineState::Up)));
}

#[test]
fn a_power_off_request_syncs_then_powers_off() {
    let mut os = FakeOs::new(vec![
        (0, vec![Event::Signal(SIGUSR2)]),
        (10, vec![Event::SyncDone]),
    ]);
    let mut machine = boot(&mut os);
    os.calls.clear();
    run_script(&mut os, &mut machine);
    let sync = os.calls.iter().position(|c| *c == Call::StartSync).unwrap();
    let reboot = os
        .calls
        .iter()
        .position(|c| *c == Call::Reboot(StopKind::PowerOff))
        .unwrap();
    assert!(sync < reboot);
    // The wait between them carried the sync bound.
    assert!(
        os.calls[sync..reboot].contains(&Call::Wait(Some(Duration::from_secs(1) + SYNC_BOUND)))
    );
}

/// I1: reboot(2) failing returns to the loop, which records it and retries.
#[test]
fn i1_a_failed_reboot_returns_to_the_loop_and_is_retried() {
    let mut os = FakeOs::new(vec![
        (0, vec![Event::Signal(SIGUSR2)]),
        (0, vec![Event::SyncDone]),
        (REBOOT_RETRY.as_millis() as u64, vec![]),
    ]);
    let mut machine = boot(&mut os);
    run_script(&mut os, &mut machine);
    let reboots = os
        .calls
        .iter()
        .filter(|c| **c == Call::Reboot(StopKind::PowerOff))
        .count();
    assert_eq!(reboots, 2);
    assert!(os.calls.iter().any(|c| matches!(
        c,
        Call::Record(r) if r.step == Some("reboot") && r.error == Some(1)
    )));
}

/// A flood of signals cannot hold off the sync bound: deadlines are checked
/// on every wakeup.
#[test]
fn deadlines_are_checked_on_every_wakeup() {
    let mut script = vec![(0, vec![Event::Signal(SIGUSR2)])];
    for _ in 0..31 {
        script.push((1000, vec![Event::Signal(1)]));
    }
    let mut os = FakeOs::new(script);
    let mut machine = boot(&mut os);
    run_script(&mut os, &mut machine);
    assert!(os.calls.contains(&Call::Reboot(StopKind::PowerOff)));
}

/// With no thread for the sync, the loop does not run it itself, unbounded;
/// it records why and goes down at once.
#[test]
fn no_sync_thread_means_no_unbounded_sync() {
    let mut os = FakeOs::new(vec![(0, vec![Event::Signal(SIGUSR2)])]);
    os.sync_errno = Some(11);
    let mut machine = boot(&mut os);
    run_script(&mut os, &mut machine);
    let sync = os.calls.iter().position(|c| *c == Call::StartSync).unwrap();
    assert!(matches!(
        &os.calls[sync + 1],
        Call::Record(r) if r.step == Some("sync") && r.result == Some("not run") && r.error == Some(11)
    ));
    assert!(os.calls[sync..].contains(&Call::Reboot(StopKind::PowerOff)));
    // No wait came between the request and the reboot.
    assert!(!os.calls[sync..].iter().any(|c| matches!(c, Call::Wait(_))));
}
