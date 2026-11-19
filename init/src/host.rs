//! The real `Os`: signalfd, waitid, the sync worker and reboot(2).

use std::os::fd::OwnedFd;
use std::sync::Arc;
use std::sync::mpsc::{self, Sender};
use std::thread;
use std::time::Duration;

use rustix::event::{EventfdFlags, PollFd, PollFlags, eventfd, poll};
use rustix::io::{Errno, read, write};
use rustix::process::{WaitId, WaitIdOptions, waitid};
use rustix::system::{RebootCommand, reboot};

use crate::clock;
use crate::console::Console;
use crate::event_loop::Os;
use crate::machine::{Event, StopKind};
use crate::record::{MachineState, Record};
use crate::signals::{self, SIGINFO_SIZE};

/// How many signalfd reads one wakeup may do, so a flood of signals cannot
/// keep the loop from its deadlines.
const MAX_SIGNAL_READS: usize = 16;

pub(crate) struct LinuxOs {
    console: Console,
    signals: Option<OwnedFd>,
    worker: Option<Worker>,
}

/// The thread that runs the calls that can block in the kernel (init.md,
/// "Implementation constraints"). In this slice that is only the shutdown
/// sync.
struct Worker {
    jobs: Sender<()>,
    done: Arc<OwnedFd>,
}

impl LinuxOs {
    /// Must be called after every signal is blocked: the worker inherits the
    /// mask, and a thread without it would take signals meant for the
    /// signalfd.
    pub(crate) fn new(mut console: Console, signals: Option<OwnedFd>) -> LinuxOs {
        let worker = match Worker::start() {
            Ok(w) => Some(w),
            Err(e) => {
                console.write(&Record {
                    step: Some("sync worker"),
                    error: Some(e.raw_os_error()),
                    ..Record::new(MachineState::Booting)
                });
                None
            }
        };
        LinuxOs {
            console,
            signals,
            worker,
        }
    }

    fn read_signals(&self, fd: &OwnedFd, events: &mut Vec<Event>) {
        let mut buf = [0u8; SIGINFO_SIZE * 8];
        for _ in 0..MAX_SIGNAL_READS {
            let Ok(n) = read(fd, &mut buf) else { return };
            // signalfd returns whole records only.
            let (records, _) = buf.get(..n).unwrap_or(&[]).as_chunks::<SIGINFO_SIZE>();
            for siginfo in records {
                events.push(Event::Signal(signals::signo_from_siginfo(siginfo)));
            }
            if n < buf.len() {
                return;
            }
        }
    }
}

impl Worker {
    fn start() -> Result<Worker, Errno> {
        let done = Arc::new(eventfd(0, EventfdFlags::CLOEXEC | EventfdFlags::NONBLOCK)?);
        let (jobs, rx) = mpsc::channel::<()>();
        let theirs = Arc::clone(&done);
        thread::Builder::new()
            .name("sync".into())
            .spawn(move || {
                for () in rx {
                    rustix::fs::sync();
                    let _ = write(&*theirs, &1u64.to_ne_bytes());
                }
            })
            .map_err(|e| Errno::from_io_error(&e).unwrap_or(Errno::AGAIN))?;
        Ok(Worker { jobs, done })
    }
}

impl Os for LinuxOs {
    fn now(&self) -> Duration {
        clock::monotonic()
    }

    fn wait(&mut self, deadline: Option<Duration>, events: &mut Vec<Event>) {
        let timeout = deadline.map(|d| clock::to_timespec(d.saturating_sub(self.now())));
        let done = self.worker.as_ref().map(|w| Arc::clone(&w.done));
        let mut fds = Vec::with_capacity(2);
        if let Some(fd) = &self.signals {
            fds.push(PollFd::new(fd, PollFlags::IN));
        }
        if let Some(fd) = &done {
            fds.push(PollFd::new(&**fd, PollFlags::IN));
        }
        if poll(&mut fds, timeout.as_ref()).is_err() {
            // Only ENOMEM is possible with signals blocked. Back off so the
            // loop does not spin; the deadline check still runs.
            let _ = rustix::thread::nanosleep(&clock::to_timespec(Duration::from_millis(100)));
            return;
        }
        let ready: Vec<bool> = fds.iter().map(|p| !p.revents().is_empty()).collect();
        drop(fds);
        let mut ready = ready.into_iter();
        if let Some(fd) = &self.signals
            && ready.next() == Some(true)
        {
            self.read_signals(fd, events);
        }
        if let Some(fd) = &done
            && ready.next() == Some(true)
        {
            let mut count = [0u8; 8];
            if read(&**fd, &mut count).is_ok() {
                events.push(Event::SyncDone);
            }
        }
    }

    fn reap(&mut self) -> u64 {
        let mut reaped = 0u64;
        loop {
            match waitid(WaitId::All, WaitIdOptions::EXITED | WaitIdOptions::NOHANG) {
                Ok(Some(_)) => reaped = reaped.saturating_add(1),
                Err(Errno::INTR) => {}
                // None: children left, none exited. ECHILD: no children.
                Ok(None) | Err(_) => return reaped,
            }
        }
    }

    fn record(&mut self, record: &Record) {
        self.console.write(record);
    }

    fn start_sync(&mut self) -> Result<(), i32> {
        if let Some(w) = &self.worker
            && w.jobs.send(()).is_ok()
        {
            return Ok(());
        }
        // The worker never started or has gone: try once more now, since the
        // sync must not run on the loop's thread, where nothing bounds it.
        let worker = Worker::start().map_err(|e| e.raw_os_error())?;
        worker
            .jobs
            .send(())
            .map_err(|_| Errno::AGAIN.raw_os_error())?;
        self.worker = Some(worker);
        Ok(())
    }

    fn reboot(&mut self, kind: StopKind) -> i32 {
        let cmd = match kind {
            StopKind::Reboot => RebootCommand::Restart,
            StopKind::PowerOff => RebootCommand::PowerOff,
        };
        match reboot(cmd) {
            Err(e) => e.raw_os_error(),
            // Restart and power-off do not return on success.
            Ok(()) => 0,
        }
    }
}
