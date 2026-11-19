//! Runs the real binary as PID 1 of a new PID and mount namespace, as root
//! through `sudo`, which is as close to a boot as CI gets without qemu (#2).
//! `reboot(2)` from a namespace's PID 1 ends the namespace instead of the
//! machine: the init is killed with SIGINT for a power-off and SIGHUP for a
//! restart, so the exit status shows which one it asked for.
//!
//! Before the init starts, a shell in the new namespace unmounts the host's
//! `/dev`, `/sys` and `/proc` there, as a real boot has none of them. The
//! kernel refuses to mount a filesystem on top of itself (`EBUSY`), and
//! devtmpfs and sysfs are one filesystem for the whole machine, so the
//! host's would otherwise block the init's.
//!
//! The tests need `sudo -n`, `unshare` and `nsenter`. Without sudo they skip,
//! unless `NL_INIT_ROOT_TESTS` is set, as CI sets it, so CI cannot pass by
//! skipping.

#![cfg(target_os = "linux")]
#![deny(unsafe_code)]
#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic,
    clippy::indexing_slicing,
    clippy::arithmetic_side_effects,
    clippy::print_stderr
)]

use std::collections::{HashMap, HashSet};
use std::io::{BufRead, BufReader};
use std::os::unix::process::ExitStatusExt;
use std::process::{Child, Command, ExitStatus, Stdio};
use std::sync::mpsc::{self, Receiver, RecvTimeoutError};
use std::thread;
use std::time::{Duration, Instant};

const INIT: &str = env!("CARGO_BIN_EXE_init");
const SIGHUP: i32 = 1;
const SIGINT: i32 = 2;
const TIMEOUT: Duration = Duration::from_secs(30);

fn root_available() -> bool {
    let ok = Command::new("sudo")
        .args(["-n", "true"])
        .stdin(Stdio::null())
        .status()
        .is_ok_and(|s| s.success());
    if !ok {
        assert!(
            std::env::var_os("NL_INIT_ROOT_TESTS").is_none(),
            "NL_INIT_ROOT_TESTS is set but `sudo -n` fails"
        );
        eprintln!("skipped: needs passwordless sudo");
    }
    ok
}

fn sudo(args: &[&str]) -> std::process::Output {
    Command::new("sudo")
        .arg("-n")
        .args(args)
        .stdin(Stdio::null())
        .output()
        .unwrap()
}

struct Pid1 {
    child: Child,
    lines: Receiver<String>,
    seen: Vec<String>,
    /// The init's pid outside its namespace.
    pid: u32,
}

impl Pid1 {
    /// Starts the init and waits for its ready line.
    fn start() -> Pid1 {
        let mut init = Pid1::spawn(&["/dev", "/sys", "/proc"]);
        init.expect(|l| l.contains(r#""machine":"up""#));
        init
    }

    /// Starts the init after unmounting `unmount` in its namespace. `exec`
    /// keeps the shell's pid, so the init is still PID 1.
    fn spawn(unmount: &[&str]) -> Pid1 {
        let script = format!("umount -l \"$@\" && exec {INIT}");
        let mut child = Command::new("sudo")
            .args([
                "-n",
                "unshare",
                "--pid",
                "--fork",
                "--mount",
                "--propagation",
                "private",
                "--kill-child",
                "--",
                "/bin/sh",
                "-c",
                &script,
                "sh",
            ])
            .args(unmount)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .spawn()
            .unwrap();
        let stdout = child.stdout.take().unwrap();
        let (tx, lines) = mpsc::channel();
        thread::spawn(move || {
            for line in BufReader::new(stdout).lines() {
                let Ok(line) = line else { return };
                // sudo may relay through a pty, which ends lines with \r\n.
                if tx.send(line.trim_end_matches('\r').to_owned()).is_err() {
                    return;
                }
            }
        });
        let pid = find_namespace_init(child.id());
        Pid1 {
            child,
            lines,
            seen: Vec::new(),
            pid,
        }
    }

    /// The next line matching `pred`. Every line must be one record.
    fn expect(&mut self, pred: impl Fn(&str) -> bool) -> String {
        let deadline = Instant::now() + TIMEOUT;
        loop {
            let left = deadline.saturating_duration_since(Instant::now());
            match self.lines.recv_timeout(left) {
                Ok(line) => {
                    assert!(
                        line.starts_with("ninelives-init {") && line.ends_with('}'),
                        "not a record: {line:?}"
                    );
                    self.seen.push(line.clone());
                    if pred(&line) {
                        return line;
                    }
                }
                Err(RecvTimeoutError::Timeout | RecvTimeoutError::Disconnected) => {
                    panic!(
                        "no matching line; saw:\n{}\n{}",
                        self.seen.join("\n"),
                        self.diagnose()
                    )
                }
            }
        }
    }

    /// What the init is doing, for a failure message.
    fn diagnose(&mut self) -> String {
        let mut out = format!("exited: {:?}\n", self.child.try_wait());
        for file in ["status", "wchan", "stack", "mountinfo"] {
            let o = sudo(&["cat", &format!("/proc/{}/{file}", self.pid)]);
            out += &format!("--- {file}\n{}\n", String::from_utf8_lossy(&o.stdout));
        }
        let o = sudo(&["ls", "-l", &format!("/proc/{}/fd", self.pid)]);
        out += &format!("--- fd\n{}", String::from_utf8_lossy(&o.stdout));
        out
    }

    fn signal(&self, name: &str) {
        let out = sudo(&["kill", "-s", name, &self.pid.to_string()]);
        assert!(out.status.success(), "kill -s {name}: {out:?}");
    }

    /// Runs a shell command inside the init's namespaces.
    fn nsenter(&self, script: &str) -> std::process::Output {
        sudo(&[
            "nsenter",
            "-t",
            &self.pid.to_string(),
            "-p",
            "-m",
            "--",
            "/bin/sh",
            "-c",
            script,
        ])
    }

    fn wait(&mut self) -> ExitStatus {
        let deadline = Instant::now() + TIMEOUT;
        loop {
            if let Some(status) = self.child.try_wait().unwrap() {
                return status;
            }
            assert!(Instant::now() < deadline, "the init did not stop");
            thread::sleep(Duration::from_millis(20));
        }
    }
}

impl Drop for Pid1 {
    fn drop(&mut self) {
        if self.child.try_wait().ok().flatten().is_none() {
            let _ = sudo(&["kill", "-s", "KILL", &self.pid.to_string()]);
            let _ = self.child.wait();
        }
    }
}

/// The descendant of `ancestor` that is PID 1 in its own namespace.
fn find_namespace_init(ancestor: u32) -> u32 {
    let deadline = Instant::now() + TIMEOUT;
    loop {
        let procs = processes();
        let mut tree: HashSet<u32> = HashSet::from([ancestor]);
        // Parents have lower pids only usually, so iterate to a fixed point.
        loop {
            let before = tree.len();
            for (pid, p) in &procs {
                if tree.contains(&p.ppid) {
                    tree.insert(*pid);
                }
            }
            if tree.len() == before {
                break;
            }
        }
        if let Some(pid) = tree
            .iter()
            .find(|pid| procs.get(pid).is_some_and(|p| p.nspid_is_init))
        {
            return *pid;
        }
        assert!(Instant::now() < deadline, "no namespace init appeared");
        thread::sleep(Duration::from_millis(20));
    }
}

struct Proc {
    ppid: u32,
    state: char,
    nspid_is_init: bool,
}

fn processes() -> HashMap<u32, Proc> {
    let mut out = HashMap::new();
    for entry in std::fs::read_dir("/proc").unwrap().flatten() {
        let Some(pid) = entry.file_name().to_str().and_then(|s| s.parse().ok()) else {
            continue;
        };
        let Ok(status) = std::fs::read_to_string(entry.path().join("status")) else {
            continue;
        };
        let field = |name: &str| {
            status
                .lines()
                .find_map(|l| l.strip_prefix(name))
                .map(str::trim)
                .unwrap_or("")
        };
        let nspid: Vec<&str> = field("NSpid:").split_whitespace().collect();
        out.insert(
            pid,
            Proc {
                ppid: field("PPid:").parse().unwrap_or(0),
                state: field("State:").chars().next().unwrap_or('?'),
                nspid_is_init: nspid.len() >= 2 && nspid.last() == Some(&"1"),
            },
        );
    }
    out
}

fn powered_off(status: ExitStatus) -> bool {
    status.signal() == Some(SIGINT) || status.code() == Some(128 + SIGINT)
}

fn restarted(status: ExitStatus) -> bool {
    status.signal() == Some(SIGHUP) || status.code() == Some(128 + SIGHUP)
}

#[test]
fn refuses_to_run_unless_pid_1() {
    let out = Command::new(INIT).output().unwrap();
    assert_eq!(out.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&out.stderr).contains("runs only as PID 1"));
    assert!(out.stdout.is_empty());
}

#[test]
fn boots_reports_up_and_powers_off_on_sigusr2() {
    if !root_available() {
        return;
    }
    let mut init = Pid1::start();
    let booting = init.seen[0].clone();
    assert!(booting.contains(r#""machine":"booting""#), "{booting}");
    assert!(!booting.contains(r#""boot":null"#), "{booting}");

    init.signal("USR2");
    init.expect(|l| l.contains(r#""signal":12,"action":"poweroff""#));
    init.expect(|l| l.contains(r#""step":"sync","result":"done""#));
    init.expect(|l| l.contains(r#""action":"poweroff","step":"reboot""#));
    let status = init.wait();
    // I1: the only way out was reboot(2).
    assert!(powered_off(status), "{status:?}");
}

#[test]
fn sigterm_and_sigint_reboot() {
    if !root_available() {
        return;
    }
    for (name, signo) in [("TERM", 15), ("INT", 2)] {
        let mut init = Pid1::start();
        init.signal(name);
        init.expect(|l| l.contains(&format!(r#""signal":{signo},"action":"reboot""#)));
        init.expect(|l| l.contains(r#""action":"reboot","step":"reboot""#));
        let status = init.wait();
        assert!(restarted(status), "SIG{name}: {status:?}");
    }
}

#[test]
fn other_signals_are_recorded_and_ignored() {
    if !root_available() {
        return;
    }
    let mut init = Pid1::start();
    for (name, signo) in [("HUP", 1), ("USR1", 10), ("QUIT", 3)] {
        init.signal(name);
        init.expect(|l| {
            l.contains(&format!(
                r#""machine":"up","signal":{signo},"action":"ignored""#
            ))
        });
    }
    assert!(init.child.try_wait().unwrap().is_none());
    init.signal("USR2");
    assert!(powered_off(init.wait()));
}

/// I2: orphans are reparented to the init and reaped at once.
#[test]
fn i2_orphans_are_reaped_promptly() {
    if !root_available() {
        return;
    }
    let mut init = Pid1::start();
    // The shell exits at once, so its three children become the init's. They
    // let go of the output pipe, so the watch below covers their exits.
    let out = init.nsenter("for i in 1 2 3; do sleep 0.3 </dev/null >/dev/null 2>&1 & done");
    assert!(out.status.success(), "{out:?}");

    // Watch for zombies of the init for a second, polling every 10 ms. None
    // may be seen in two polls 100 ms or more apart.
    let mut first_seen: HashMap<u32, Instant> = HashMap::new();
    let end = Instant::now() + Duration::from_secs(1);
    while Instant::now() < end {
        for (pid, p) in processes() {
            if p.ppid == init.pid && p.state == 'Z' {
                let since = *first_seen.entry(pid).or_insert_with(Instant::now);
                assert!(
                    since.elapsed() < Duration::from_millis(100),
                    "zombie {pid} outlived 100 ms"
                );
            }
        }
        thread::sleep(Duration::from_millis(10));
    }
    let children = processes()
        .into_values()
        .filter(|p| p.ppid == init.pid)
        .count();
    assert_eq!(children, 0, "the init still has children");

    init.signal("USR2");
    init.expect(|l| l.contains(r#""action":"poweroff","step":"reboot","orphans_reaped":3"#));
    assert!(powered_off(init.wait()));
}

/// The mounts in init.md's step 1, with their options, and step 7's
/// read-only /proc/sys (I19).
#[test]
fn mounts_match_init_md_and_proc_sys_is_read_only() {
    if !root_available() {
        return;
    }
    let mut init = Pid1::start();
    let out = sudo(&["cat", &format!("/proc/{}/mountinfo", init.pid)]);
    assert!(out.status.success(), "{out:?}");
    let mountinfo = String::from_utf8(out.stdout).unwrap();

    // The last entry for a mount point is the one on top.
    let top = |target: &str| -> (Vec<String>, String, Vec<String>) {
        let line = mountinfo
            .lines()
            .rfind(|l| l.split(' ').nth(4) == Some(target))
            .unwrap_or_else(|| panic!("{target} is not mounted:\n{mountinfo}"));
        let (left, right) = line.split_once(" - ").unwrap();
        let opts = left.split(' ').nth(5).unwrap();
        let mut right = right.split(' ');
        let fstype = right.next().unwrap().to_owned();
        let superopts = right.nth(1).unwrap_or("");
        (
            opts.split(',').map(str::to_owned).collect(),
            fstype,
            superopts.split(',').map(str::to_owned).collect(),
        )
    };
    let has = |set: &[String], want: &[&str]| want.iter().all(|w| set.iter().any(|s| s == w));

    // tmpfs leaves its default mode, 1777, out of mountinfo, so /tmp's is
    // not listed.
    for (target, fstype, opts, superopts) in [
        (
            "/proc",
            "proc",
            &["nosuid", "nodev", "noexec"][..],
            &["hidepid=invisible"][..],
        ),
        ("/sys", "sysfs", &["nosuid", "nodev", "noexec"], &[]),
        ("/dev", "devtmpfs", &["nosuid", "noexec"], &[]),
        (
            "/sys/fs/cgroup",
            "cgroup2",
            &["nosuid", "nodev", "noexec"],
            &["nsdelegate", "memory_recursiveprot"],
        ),
        (
            "/run",
            "tmpfs",
            &["nosuid", "nodev", "noexec"],
            &["mode=755", "size=16384k"],
        ),
        (
            "/tmp",
            "tmpfs",
            &["nosuid", "nodev", "noexec"],
            &["size=65536k"],
        ),
        (
            "/proc/sys",
            "proc",
            &["ro", "nosuid", "nodev", "noexec"],
            &[],
        ),
    ] {
        let (got_opts, got_type, got_super) = top(target);
        assert_eq!(got_type, fstype, "{target}");
        assert!(has(&got_opts, opts), "{target}: {got_opts:?}");
        assert!(has(&got_super, superopts), "{target}: {got_super:?}");
        if target == "/dev" {
            assert!(!got_opts.iter().any(|o| o == "nodev"), "/dev: {got_opts:?}");
        }
    }

    // Writing a sysctl's own value back is harmless even if this test fails.
    let out =
        init.nsenter("v=$(cat /proc/sys/vm/swappiness) && echo \"$v\" > /proc/sys/vm/swappiness");
    assert!(!out.status.success(), "a sysctl write succeeded");
    assert!(
        String::from_utf8_lossy(&out.stderr).contains("Read-only file system"),
        "{out:?}"
    );

    init.signal("USR2");
    assert!(powered_off(init.wait()));
}

/// A failed early mount: `emergency` with the step and errno, nothing marked
/// up, /proc/sys still locked because /proc was mounted (I19), and a stop
/// request still powers off.
#[test]
fn a_failed_mount_is_an_emergency_that_still_locks_and_stops() {
    if !root_available() {
        return;
    }
    // The host's sysfs stays, so mounting /sys fails with EBUSY.
    let mut init = Pid1::spawn(&["/dev", "/proc"]);
    let line = init.expect(|l| l.contains(r#""machine":"emergency""#));
    assert!(
        line.contains(r#""step":"mount /sys","error":"EBUSY""#),
        "{line}"
    );
    assert!(!init.seen.iter().any(|l| l.contains(r#""machine":"up""#)));

    let out =
        init.nsenter("v=$(cat /proc/sys/vm/swappiness) && echo \"$v\" > /proc/sys/vm/swappiness");
    assert!(!out.status.success(), "a sysctl write succeeded");
    assert!(
        String::from_utf8_lossy(&out.stderr).contains("Read-only file system"),
        "{out:?}"
    );

    init.signal("USR2");
    init.expect(|l| l.contains(r#""action":"poweroff","step":"reboot""#));
    assert!(powered_off(init.wait()));
}

/// What `/proc/<pid>/status` says about a process, read as root.
fn status(pid: u32) -> HashMap<String, String> {
    let out = sudo(&["cat", &format!("/proc/{pid}/status")]);
    String::from_utf8_lossy(&out.stdout)
        .lines()
        .filter_map(|l| l.split_once(':'))
        .map(|(k, v)| (k.to_owned(), v.trim().to_owned()))
        .collect()
}

/// S24, I17: PID 1 is not root after boot and keeps only CAP_KILL and
/// CAP_SYS_BOOT, permitted. The kernel's own view, not the init's.
#[test]
fn pid1_drops_root_and_keeps_two_capabilities() {
    if !root_available() {
        return;
    }
    let mut init = Pid1::start();
    let status = status(init.pid);
    assert_eq!(status["Uid"], "900\t900\t900\t900", "{status:?}");
    assert_eq!(status["Gid"], "900\t900\t900\t900", "{status:?}");
    assert_eq!(status["Groups"], "", "{status:?}");
    assert_eq!(status["CapPrm"], "0000000000400020", "{status:?}");
    assert_eq!(status["CapEff"], "0000000000000000", "{status:?}");
    assert_eq!(status["CapInh"], "0000000000000000", "{status:?}");
    assert_eq!(status["CapAmb"], "0000000000000000", "{status:?}");
    assert_eq!(status["CapBnd"], "0000000000400020", "{status:?}");
    assert_eq!(status["NoNewPrivs"], "1", "{status:?}");

    let drop = init
        .seen
        .iter()
        .find(|l| l.contains(r#""step":"drop root""#))
        .unwrap();
    assert!(drop.contains(r#""uid":[900,900,900]"#), "{drop}");
    assert!(drop.contains(r#""securebits":15"#), "{drop}");

    // The drop leaves a PID 1 that can still reboot: reboot(2) needs the
    // capability it raises for the call.
    init.signal("USR2");
    init.expect(|l| l.contains(r#""action":"poweroff","step":"reboot""#));
    assert!(powered_off(init.wait()));
}

/// The drop happens in an emergency too: no root PID 1, whatever failed.
#[test]
fn an_emergency_machine_has_dropped_root_as_well() {
    if !root_available() {
        return;
    }
    let mut init = Pid1::spawn(&["/dev", "/proc"]);
    init.expect(|l| l.contains(r#""machine":"emergency""#));
    assert!(
        init.seen
            .iter()
            .any(|l| l.contains(r#""step":"drop root""#)),
        "{:?}",
        init.seen
    );
    assert_eq!(status(init.pid)["Uid"], "900\t900\t900\t900");
    init.signal("USR2");
    assert!(powered_off(init.wait()));
}
