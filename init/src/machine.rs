//! The decision logic: a pure state machine over events that returns actions
//! (init.md, "Implementation constraints"). Nothing here makes a system call,
//! so the invariants it carries are tested without a kernel.
//!
//! This slice has no services, so a stop request goes straight to the final
//! steps of "Ordered shutdown": sync, bounded, then `reboot(2)`.

use std::time::Duration;

use crate::record::{MachineState, Record};
use crate::signals::{self, Meaning};

/// Final steps 2 to 5 of the ordered shutdown, together.
pub(crate) const SYNC_BOUND: Duration = Duration::from_secs(30);
/// How often an emergency repeats its reason on the console.
pub(crate) const EMERGENCY_REPEAT: Duration = Duration::from_secs(30);
/// How long after a failed `reboot(2)` it is tried again. PID 1 cannot exit
/// (I1), so retrying is the only way forward.
pub(crate) const REBOOT_RETRY: Duration = Duration::from_secs(30);

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum StopKind {
    Reboot,
    PowerOff,
}

impl StopKind {
    pub(crate) fn as_str(self) -> &'static str {
        match self {
            StopKind::Reboot => "reboot",
            StopKind::PowerOff => "poweroff",
        }
    }
}

/// A boot step that failed, with its errno.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct Failure {
    pub(crate) step: &'static str,
    pub(crate) errno: i32,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum BootOutcome {
    Up,
    /// A fixed early mount or the `/proc/sys` lockdown failed. The machine
    /// stays up doing nothing else, repeats the reason, and still reaps and
    /// handles stop requests.
    Emergency(Failure),
    /// The loop cannot hear stop requests, so it reports and powers off.
    PowerOff(Failure),
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Event {
    /// A signal read from the signalfd.
    Signal(u32),
    /// The number of children the last reap collected.
    Reaped(u64),
    /// The worker finished the shutdown sync.
    SyncDone,
    /// No thread could run the sync; this errno says why.
    SyncFailed(i32),
    /// `reboot(2)` returned this errno.
    RebootFailed(i32),
    /// The loop woke up; deadlines are checked against the time.
    Tick,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Action {
    /// `waitid` until no exited child is left.
    Reap,
    Record(Record),
    /// Hand the sync to the worker thread, which reports `SyncDone`.
    StartSync,
    Reboot(StopKind),
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Phase {
    Up,
    Emergency {
        failure: Failure,
        repeat_at: Duration,
    },
    Syncing {
        kind: StopKind,
        deadline: Duration,
    },
    Rebooting {
        kind: StopKind,
        retry_at: Option<Duration>,
    },
}

#[derive(Debug)]
pub(crate) struct Machine {
    phase: Phase,
    orphans_reaped: u64,
}

impl Machine {
    /// The machine after the boot steps, and its first actions.
    pub(crate) fn start(outcome: BootOutcome, now: Duration) -> (Machine, Vec<Action>) {
        let mut machine = Machine {
            phase: Phase::Up,
            orphans_reaped: 0,
        };
        // A child that exited before the signal mask was set raised a SIGCHLD
        // the kernel dropped, so reap once before waiting for any (I2).
        let mut actions = vec![Action::Reap];
        match outcome {
            BootOutcome::Up => actions.push(Action::Record(Record::new(MachineState::Up))),
            BootOutcome::Emergency(failure) => {
                actions.push(Action::Record(emergency_record(failure)));
                machine.phase = Phase::Emergency {
                    failure,
                    repeat_at: now.saturating_add(EMERGENCY_REPEAT),
                };
            }
            BootOutcome::PowerOff(failure) => {
                actions.push(Action::Record(emergency_record(failure)));
                machine.begin_stop(StopKind::PowerOff, None, now, &mut actions);
            }
        }
        (machine, actions)
    }

    pub(crate) fn handle(&mut self, now: Duration, event: Event) -> Vec<Action> {
        let mut actions = Vec::new();
        match event {
            Event::Signal(signo) => self.on_signal(signo, now, &mut actions),
            Event::Reaped(n) => self.orphans_reaped = self.orphans_reaped.saturating_add(n),
            Event::SyncDone => {
                // A sync that finishes after its bound passed changes nothing.
                if let Phase::Syncing { kind, .. } = self.phase {
                    actions.push(Action::Record(Record {
                        step: Some("sync"),
                        result: Some("done"),
                        ..Record::new(MachineState::Stopping)
                    }));
                    self.reboot(kind, &mut actions);
                }
            }
            Event::SyncFailed(errno) => {
                // Running sync() on the loop's thread would hold the loop
                // without a bound, so the machine goes down without it, as
                // after a passed bound, and says so first.
                if let Phase::Syncing { kind, .. } = self.phase {
                    actions.push(Action::Record(Record {
                        step: Some("sync"),
                        result: Some("not run"),
                        error: Some(errno),
                        ..Record::new(MachineState::Stopping)
                    }));
                    self.reboot(kind, &mut actions);
                }
            }
            Event::RebootFailed(errno) => {
                if let Phase::Rebooting { kind, .. } = self.phase {
                    actions.push(Action::Record(Record {
                        step: Some("reboot"),
                        error: Some(errno),
                        ..Record::new(MachineState::Stopping)
                    }));
                    self.phase = Phase::Rebooting {
                        kind,
                        retry_at: Some(now.saturating_add(REBOOT_RETRY)),
                    };
                }
            }
            Event::Tick => self.on_tick(now, &mut actions),
        }
        actions
    }

    /// When the loop must wake up even if nothing happens.
    pub(crate) fn next_deadline(&self) -> Option<Duration> {
        match self.phase {
            Phase::Up => None,
            Phase::Emergency { repeat_at, .. } => Some(repeat_at),
            Phase::Syncing { deadline, .. } => Some(deadline),
            Phase::Rebooting { retry_at, .. } => retry_at,
        }
    }

    pub(crate) fn state(&self) -> MachineState {
        match self.phase {
            Phase::Up => MachineState::Up,
            Phase::Emergency { .. } => MachineState::Emergency,
            Phase::Syncing { .. } | Phase::Rebooting { .. } => MachineState::Stopping,
        }
    }

    fn on_signal(&mut self, signo: u32, now: Duration, actions: &mut Vec<Action>) {
        match signals::meaning(signo) {
            Meaning::Reap => actions.push(Action::Reap),
            Meaning::Ignore => actions.push(Action::Record(Record {
                signal: Some(signo),
                action: Some("ignored"),
                ..Record::new(self.state())
            })),
            Meaning::Stop(requested) => match &mut self.phase {
                Phase::Up | Phase::Emergency { .. } => {
                    self.begin_stop(requested, Some(signo), now, actions);
                }
                Phase::Syncing { kind, .. } | Phase::Rebooting { kind, .. } => {
                    // A request during shutdown can only turn a reboot into a
                    // power-off, the more conservative of the two.
                    if requested == StopKind::PowerOff {
                        *kind = StopKind::PowerOff;
                    }
                    actions.push(Action::Record(Record {
                        signal: Some(signo),
                        action: Some(kind.as_str()),
                        ..Record::new(MachineState::Stopping)
                    }));
                }
            },
        }
    }

    fn on_tick(&mut self, now: Duration, actions: &mut Vec<Action>) {
        match self.phase {
            Phase::Emergency { failure, repeat_at } if now >= repeat_at => {
                actions.push(Action::Record(emergency_record(failure)));
                self.phase = Phase::Emergency {
                    failure,
                    repeat_at: now.saturating_add(EMERGENCY_REPEAT),
                };
            }
            Phase::Syncing { kind, deadline } if now >= deadline => {
                // A disk that hangs must not keep the machine half shut down;
                // the bound is recorded before reboot(2) (I10).
                actions.push(Action::Record(Record {
                    step: Some("sync"),
                    result: Some("bound passed"),
                    ..Record::new(MachineState::Stopping)
                }));
                self.reboot(kind, actions);
            }
            Phase::Rebooting {
                kind,
                retry_at: Some(retry_at),
            } if now >= retry_at => self.reboot(kind, actions),
            _ => {}
        }
    }

    fn begin_stop(
        &mut self,
        kind: StopKind,
        signal: Option<u32>,
        now: Duration,
        actions: &mut Vec<Action>,
    ) {
        actions.push(Action::Record(Record {
            signal,
            action: Some(kind.as_str()),
            ..Record::new(MachineState::Stopping)
        }));
        // TODO(#48): stop services from the highest phase down, then kill
        // every service group, before the final steps below.
        // TODO(#4): syncfs the $DATA fd opened at mount ahead of sync().
        actions.push(Action::StartSync);
        self.phase = Phase::Syncing {
            kind,
            deadline: now.saturating_add(SYNC_BOUND),
        };
    }

    fn reboot(&mut self, kind: StopKind, actions: &mut Vec<Action>) {
        // The final record goes out before reboot(2), which does not return.
        actions.push(Action::Record(Record {
            step: Some("reboot"),
            action: Some(kind.as_str()),
            orphans_reaped: Some(self.orphans_reaped),
            ..Record::new(MachineState::Stopping)
        }));
        actions.push(Action::Reboot(kind));
        self.phase = Phase::Rebooting {
            kind,
            retry_at: None,
        };
    }
}

pub(crate) fn emergency_record(failure: Failure) -> Record {
    Record {
        step: Some(failure.step),
        error: Some(failure.errno),
        ..Record::new(MachineState::Emergency)
    }
}

#[cfg(test)]
mod tests;
