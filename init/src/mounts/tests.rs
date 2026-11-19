use super::*;

fn find(target: &str) -> (usize, &'static Mount) {
    EARLY
        .iter()
        .enumerate()
        .find(|(_, m)| m.target == target)
        .unwrap_or_else(|| panic!("{target} is not in the table"))
}

#[test]
fn every_mount_in_init_md_is_present_with_its_type() {
    for (target, fstype) in [
        ("/proc", "proc"),
        ("/sys", "sysfs"),
        ("/dev", "devtmpfs"),
        ("/sys/fs/cgroup", "cgroup2"),
        ("/run", "tmpfs"),
        ("/tmp", "tmpfs"),
    ] {
        assert_eq!(find(target).1.fstype, fstype, "{target}");
    }
    assert_eq!(EARLY.len(), 6);
}

#[test]
fn every_mount_is_nosuid_and_noexec() {
    for m in &EARLY {
        assert!(m.flags.nosuid, "{} lacks nosuid", m.target);
        assert!(m.flags.noexec, "{} lacks noexec", m.target);
    }
}

#[test]
fn only_dev_allows_device_nodes() {
    for m in &EARLY {
        assert_eq!(m.flags.nodev, m.target != "/dev", "{}", m.target);
    }
}

#[test]
fn options_match_init_md() {
    assert_eq!(find("/proc").1.data, "hidepid=invisible");
    assert_eq!(
        find("/sys/fs/cgroup").1.data,
        "nsdelegate,memory_recursiveprot"
    );
    assert_eq!(find("/run").1.data, "mode=0755,size=16m");
    assert_eq!(find("/tmp").1.data, "mode=1777,size=64m");
    assert_eq!(find("/dev").1.data, "");
    assert_eq!(find("/sys").1.data, "");
}

#[test]
fn dev_is_first_so_failures_after_it_reach_the_console() {
    assert_eq!(EARLY[0].target, "/dev");
}

#[test]
fn cgroup_is_mounted_after_sys() {
    assert!(find("/sys").0 < find("/sys/fs/cgroup").0);
}

#[test]
fn proc_is_mounted_before_proc_sys_is_locked() {
    // The lockdown runs after the whole table, so /proc only has to be in it.
    find("/proc");
    assert!(PROC_SYS.starts_with("/proc/"));
}

#[test]
fn read_only_proc_sys_keeps_proc_flags() {
    assert_eq!(PROC_SYS_FLAGS, find("/proc").1.flags);
}

#[test]
fn each_step_names_its_target() {
    for m in &EARLY {
        assert_eq!(m.step, format!("mount {}", m.target));
    }
}

#[test]
fn targets_are_absolute_and_unique() {
    for (i, m) in EARLY.iter().enumerate() {
        assert!(m.target.starts_with('/'));
        assert!(EARLY.iter().skip(i + 1).all(|o| o.target != m.target));
    }
}
