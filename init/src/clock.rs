//! The two clocks records carry. Deadlines use only the monotonic one, so a
//! clock step changes no decision.

use std::time::Duration;

use rustix::time::{ClockId, Timespec, clock_gettime};

pub(crate) fn monotonic() -> Duration {
    to_duration(clock_gettime(ClockId::Monotonic)).unwrap_or(Duration::ZERO)
}

pub(crate) fn realtime() -> Option<Duration> {
    to_duration(clock_gettime(ClockId::Realtime))
}

pub(crate) fn to_timespec(d: Duration) -> Timespec {
    Timespec {
        tv_sec: i64::try_from(d.as_secs()).unwrap_or(i64::MAX),
        tv_nsec: i64::from(d.subsec_nanos()),
    }
}

fn to_duration(t: Timespec) -> Option<Duration> {
    let secs = u64::try_from(t.tv_sec).ok()?;
    let nanos = u64::try_from(t.tv_nsec).ok()?;
    Duration::from_secs(secs).checked_add(Duration::from_nanos(nanos))
}
