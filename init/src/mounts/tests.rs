use std::collections::BTreeSet;

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

/// Options that init.md gives and this slice leaves for later.
const DEFERRED: &[(&str, &str)] = &[
    // #48: the group's number comes from the config.
    ("/proc", "gid=nl-inspect"),
];

fn ours(m: &Mount) -> BTreeSet<String> {
    let mut set: BTreeSet<String> = m
        .data
        .split(',')
        .filter(|o| !o.is_empty())
        .map(str::to_owned)
        .collect();
    for (on, name) in [
        (m.flags.nosuid, "nosuid"),
        (m.flags.nodev, "nodev"),
        (m.flags.noexec, "noexec"),
    ] {
        if on {
            set.insert(name.to_owned());
        }
    }
    set
}

fn init_md() -> String {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/../docs/components/init.md");
    std::fs::read_to_string(path).unwrap()
}

/// The rows of step 1's table: target, type and options.
fn step_1_table(doc: &str) -> Vec<(String, String, String)> {
    let cell = |c: &str| c.trim().trim_matches('`').to_owned();
    doc.lines()
        .map(str::trim)
        .skip_while(|l| *l != "| Target | Type | Options | Why |")
        .skip(2)
        .take_while(|l| l.starts_with('|'))
        .map(|l| {
            let cells: Vec<&str> = l.trim_matches('|').split('|').collect();
            (cell(cells[0]), cell(cells[1]), cell(cells[2]))
        })
        .collect()
}

/// The `[[mount]]` entry for /tmp in init.md's example config.
fn example_tmp(doc: &str) -> (String, String, String) {
    let at = doc
        .find("target = \"/tmp\"")
        .expect("no /tmp in the example config");
    let value = |key: &str| {
        let line = doc[at..].lines().find(|l| l.starts_with(key)).unwrap();
        line.split('"').nth(1).unwrap().to_owned()
    };
    ("/tmp".to_owned(), value("fstype"), value("options"))
}

/// Read from docs/components/init.md, so the table cannot drift from it.
#[test]
fn every_mount_matches_init_md() {
    let doc = init_md();
    let mut expected = step_1_table(&doc);
    assert_eq!(expected.len(), 5, "{expected:?}");
    expected.push(example_tmp(&doc));
    for (target, fstype, options) in expected {
        let (_, m) = find(&target);
        assert_eq!(m.fstype, fstype, "{target}");
        let want: BTreeSet<String> = options
            .split(',')
            .filter(|o| !DEFERRED.contains(&(target.as_str(), *o)))
            .map(str::to_owned)
            .collect();
        assert_eq!(ours(m), want, "{target}");
    }
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
