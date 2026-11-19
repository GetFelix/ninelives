//! The fixed early mounts (init.md, "Boot, as root", step 1) and the
//! `/proc/sys` lockdown (step 7). The table is data so it can be checked
//! without a kernel; `boot` performs it.

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct Flags {
    pub(crate) nosuid: bool,
    pub(crate) nodev: bool,
    pub(crate) noexec: bool,
}

const ALL: Flags = Flags {
    nosuid: true,
    nodev: true,
    noexec: true,
};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct Mount {
    /// Used in records, so a failure names the mount.
    pub(crate) step: &'static str,
    pub(crate) target: &'static str,
    pub(crate) fstype: &'static str,
    pub(crate) flags: Flags,
    pub(crate) data: &'static str,
}

/// In order. `/dev` comes first, ahead of init.md's table order, because the
/// root has no device nodes and nothing can reach the console until it is
/// mounted; a failure in any later mount is then reported. The cgroup
/// hierarchy needs `/sys`.
pub(crate) const EARLY: [Mount; 6] = [
    Mount {
        step: "mount /dev",
        target: "/dev",
        fstype: "devtmpfs",
        // Device nodes are the point of /dev, so no nodev.
        flags: Flags {
            nosuid: true,
            nodev: false,
            noexec: true,
        },
        data: "",
    },
    Mount {
        step: "mount /proc",
        target: "/proc",
        fstype: "proc",
        // TODO(#48): add gid=<nl-inspect> once the config names that group.
        // Without it only a process's own uid sees it, which is stricter.
        flags: ALL,
        data: "hidepid=invisible",
    },
    Mount {
        step: "mount /sys",
        target: "/sys",
        fstype: "sysfs",
        flags: ALL,
        data: "",
    },
    Mount {
        step: "mount /sys/fs/cgroup",
        target: "/sys/fs/cgroup",
        fstype: "cgroup2",
        flags: ALL,
        data: "nsdelegate,memory_recursiveprot",
    },
    Mount {
        step: "mount /run",
        target: "/run",
        fstype: "tmpfs",
        flags: ALL,
        data: "mode=0755,size=16m",
    },
    // TODO(#48): /tmp is a `[[mount]]` entry in the config (step 3). Until the
    // config is read it is fixed here, with the options init.md's example gives.
    Mount {
        step: "mount /tmp",
        target: "/tmp",
        fstype: "tmpfs",
        flags: ALL,
        data: "mode=1777,size=64m",
    },
];

/// Bind-remounted read-only so no later process can change a sysctl,
/// whatever capabilities it holds (I19, S24).
pub(crate) const PROC_SYS: &str = "/proc/sys";

/// The flags the read-only `/proc/sys` keeps: those of `/proc` plus `ro`.
/// A bind remount replaces every per-mount flag, so dropping these would
/// loosen the mount.
pub(crate) const PROC_SYS_FLAGS: Flags = ALL;

#[cfg(test)]
mod tests;
