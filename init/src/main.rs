//! The NineLives init, PID 1. The design is
//! [docs/components/init.md](../../docs/components/init.md); this slice
//! implements the part the M0 image needs (see README.md).
//!
//! The decision logic (`machine`, `signals`, `mounts`, `record`) is pure and
//! builds on any OS. The rest is Linux-only and talks to the kernel; all of
//! its `unsafe` is in `sys`.

#![deny(unsafe_code)]
#![cfg_attr(all(target_os = "linux", not(test)), no_main)]
#![cfg_attr(any(test, not(target_os = "linux")), allow(dead_code))]

mod boot_plan;
mod event_loop;
mod machine;
mod mounts;
mod record;
mod signals;

#[cfg(all(target_os = "linux", not(test)))]
mod boot;
#[cfg(all(target_os = "linux", not(test)))]
mod clock;
#[cfg(all(target_os = "linux", not(test)))]
mod console;
#[cfg(all(target_os = "linux", not(test)))]
mod host;
#[cfg(all(target_os = "linux", not(test)))]
#[allow(unsafe_code)]
mod sys;

/// Called from `sys::main`. As PID 1 the init ignores its arguments and
/// environment: the kernel fills them from the command line, which must not
/// change what the init does.
#[cfg(all(target_os = "linux", not(test)))]
fn entry() -> ! {
    refuse_unless_pid1();
    console::install_panic_hook();
    let booted = boot::run();
    let mut os = host::LinuxOs::new(booted.console, booted.signals);
    let (mut machine, first) = machine::Machine::start(booted.outcome, event_loop::Os::now(&os));
    event_loop::run(&mut os, &mut machine, first)
}

/// Started any other way, the init would mount over the host's /dev and
/// reboot it. This is the one place it may exit.
#[cfg(all(target_os = "linux", not(test)))]
#[allow(clippy::exit, clippy::print_stderr)]
fn refuse_unless_pid1() {
    if !rustix::process::getpid().is_init() {
        eprintln!("init: runs only as PID 1");
        std::process::exit(2);
    }
}

#[cfg(all(not(target_os = "linux"), not(test)))]
#[allow(clippy::exit, clippy::print_stderr)]
fn main() {
    eprintln!("init: runs only on Linux, as PID 1");
    std::process::exit(2);
}
