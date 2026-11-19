//! The single-threaded event loop: wait, feed events to the machine, carry
//! out its actions. Everything the loop asks of the kernel goes through `Os`,
//! so the loop itself runs in unit tests against a fake.

use std::collections::VecDeque;
use std::time::Duration;

use crate::machine::{Action, Event, Machine, StopKind};
use crate::record::Record;

pub(crate) trait Os {
    /// `CLOCK_MONOTONIC`.
    fn now(&self) -> Duration;
    /// Blocks until at least one event or the deadline, then appends what
    /// arrived. It must not block on anything but the wait itself (I14).
    fn wait(&mut self, deadline: Option<Duration>, events: &mut Vec<Event>);
    /// Reaps every exited child and returns how many.
    fn reap(&mut self) -> u64;
    fn record(&mut self, record: &Record);
    fn start_sync(&mut self);
    /// Returns only if `reboot(2)` failed, with its errno.
    fn reboot(&mut self, kind: StopKind) -> i32;
}

/// PID 1's loop. It has no way out but `reboot(2)` (I1).
pub(crate) fn run(os: &mut impl Os, machine: &mut Machine, first: Vec<Action>) -> ! {
    let now = os.now();
    execute_all(os, machine, now, first);
    let mut events = Vec::with_capacity(16);
    loop {
        step(os, machine, &mut events);
    }
}

/// One iteration: every event read is handled before the next wait, so a
/// SIGCHLD is always followed by its reap in the same iteration (I2).
pub(crate) fn step(os: &mut impl Os, machine: &mut Machine, events: &mut Vec<Event>) {
    events.clear();
    os.wait(machine.next_deadline(), events);
    // Deadlines are checked on every wakeup, not only on a timeout, so a
    // stream of signals cannot starve the sync bound.
    events.push(Event::Tick);
    let now = os.now();
    for event in events.drain(..) {
        let actions = machine.handle(now, event);
        execute_all(os, machine, now, actions);
    }
}

fn execute_all(os: &mut impl Os, machine: &mut Machine, now: Duration, actions: Vec<Action>) {
    // Actions can report back (a reap count, a failed reboot); those events
    // are handled here, at once, before the loop waits again.
    let mut queue: VecDeque<Action> = actions.into();
    while let Some(action) = queue.pop_front() {
        let follow_up = match action {
            Action::Reap => Some(Event::Reaped(os.reap())),
            Action::Record(record) => {
                os.record(&record);
                None
            }
            Action::StartSync => {
                os.start_sync();
                None
            }
            Action::Reboot(kind) => Some(Event::RebootFailed(os.reboot(kind))),
        };
        if let Some(event) = follow_up {
            queue.extend(machine.handle(now, event));
        }
    }
}

#[cfg(test)]
mod tests;
