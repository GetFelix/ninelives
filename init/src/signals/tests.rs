use super::*;

#[test]
fn the_four_signals_have_three_meanings() {
    assert_eq!(meaning(SIGCHLD), Meaning::Reap);
    assert_eq!(meaning(SIGINT), Meaning::Stop(StopKind::Reboot));
    assert_eq!(meaning(SIGTERM), Meaning::Stop(StopKind::Reboot));
    assert_eq!(meaning(SIGUSR2), Meaning::Stop(StopKind::PowerOff));
}

#[test]
fn every_other_signal_is_ignored() {
    for signo in 0..=64 {
        if [SIGCHLD, SIGINT, SIGTERM, SIGUSR2].contains(&signo) {
            continue;
        }
        assert_eq!(meaning(signo), Meaning::Ignore, "signal {signo}");
    }
    // SIGHUP, SIGQUIT, SIGABRT, SIGUSR1, SIGSEGV, SIGPWR and real-time signals.
    for signo in [1, 3, 6, 10, 11, 30, 34, 64, u32::MAX] {
        assert_eq!(meaning(signo), Meaning::Ignore);
    }
}

#[test]
fn siginfo_yields_its_first_field() {
    let mut info = [0u8; SIGINFO_SIZE];
    info[..4].copy_from_slice(&SIGUSR2.to_ne_bytes());
    info[4..8].copy_from_slice(&99u32.to_ne_bytes());
    assert_eq!(signo_from_siginfo(&info), SIGUSR2);
}
