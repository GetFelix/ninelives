# init (PID 1)

The scope is fixed by [0002](../decisions/0002-our-own-init.md). This page
says how the init meets it. Nothing here is built yet; the first slice is
[#47](https://github.com/GetFelix/ninelives/issues/47) (M0) and the services
and shutdown are [#48](https://github.com/GetFelix/ninelives/issues/48) (M1).

Paths below use `$DATA` for the data partition's mount point, which the
image config names. Requirement S24 (no root after early boot) and the
per-service capability list in S6 are being added with the threat model
([#56](https://github.com/GetFelix/ninelives/issues/56)); this page cites
them by those numbers.

## What it does

The init is the first process the kernel starts. While it still has the
uid 0 the kernel gave it, it mounts the kernel filesystems and the data
partition, builds the cgroup tree, creates the directories services need,
and forks one launcher per service, each of which drops at once to that
service's uid. Then the init drops to its own unprivileged user for good,
keeping only `CAP_KILL` and `CAP_SYS_BOOT`, before any service runs. From
then on no process on the machine is root (S24). It reaps every orphan, has
the launchers start the services the machine's role needs in a fixed order
of phases, each in its own cgroup v2 group with its output captured, and
restarts a service that exits, with capped exponential backoff. On a reboot
or power-off request, including the platform's power button, it stops
services in reverse order within the platform's stop budget, giving Felix
priority over felixd, syncs the data partition and calls `reboot(2)`.
Throughout, it writes its own state and each service's state as records to
the console, to `/run` and to the data partition. It never talks to Felix.

## What it does not do

Each of these is left out on purpose. Adding one needs a change to 0002.

- **No dependency graph, socket activation or on-demand start.** Services
  start in numbered phases. That is enough for the handful of services a
  role has, and a graph solver in PID 1 is code that can be wrong at boot.
- **No readiness protocol.** A phase starts when the earlier phase's
  long-running services have been started, not when they are ready. Felix
  has no readiness signal (see [Open questions](#open-questions)), and
  felixd exits and is restarted when Felix is not up yet
  ([felixd-contract.md](../felixd-contract.md#starting)).
- **No health checks or liveness probes.** A service is restarted when it
  exits, nothing else. Deciding that a running process is unhealthy is
  policy, and policy belongs on the log (M4).
- **No reload, and no services added after boot.** The config is read once.
  A new config is a new image.
- **No cluster decisions.** The init never drains, reboots because a service
  is failing, or changes the machine's role.
- **No Felix client.** PID 1 must not depend on what it starts. Shipping
  records to Felix is a later service's job.
- **No device management.** No udev, no hotplug, no module loading (there are
  none, [0007](../decisions/0007-kernel-from-upstream-source.md)). devtmpfs
  creates the device nodes.
- **No filesystem creation, checking or resizing, and no mounts after boot.**
  The init mounts a filesystem that exists, once, before it drops privilege.
  Creating and growing the data partition is
  [#4](https://github.com/GetFelix/ninelives/issues/4)'s design and runs as a
  separate program. The init never formats anything, because formatting the
  wrong disk destroys Felix's data.
- **No unmount at shutdown.** `syncfs` and the filesystem's journal give the
  same durability without `CAP_SYS_ADMIN` (see
  [Ordered shutdown](#ordered-shutdown)).
- **No network configuration beyond loopback.** DHCP, time sync and the
  packet filter are services.
- **No login, getty, shell or user sessions, and no root shell anywhere**,
  including the emergency state and development images (S24). A development
  image that adds a serial shell runs it as an unprivileged service.
- **No sockets to the outside.** The init listens on nothing and connects to
  nothing. Its only sockets are the socketpairs to its own launchers.
- **No timers, cron, suspend, hibernate or kexec.**
- **No per-service mount or network namespaces.** Separate users, a
  read-only root, project quotas, Landlock and seccomp give the isolation S6
  asks for without mount propagation rules to get right. This is reopened if
  the threat model finds a case they miss.
- **No boot counting or A/B fallback.** That is M3
  ([#8](https://github.com/GetFelix/ninelives/issues/8)).
- **No kernel crash dumps.** S11's crash dumps need their own design.

## Interfaces

### Programs it starts

[0002](../decisions/0002-our-own-init.md) leaves the helper programs to this
design. Every one runs under its own non-zero uid from its first instruction
(S24).

| Service | Program | Roles | Phase | Why this program |
|---|---|---|---|---|
| firewall | the packet filter loader, [open](#open-questions) | all | 0, run once | Loads the S7 ruleset before any interface is configured, so no packet arrives unfiltered. |
| time | `chronyd` (Debian `chrony`) | all | 1 | Runs in the foreground (`-d`), starts non-root with only `CAP_SYS_TIME` (`-U`), ships its own seccomp filter (`-F 1`), opens no port (`port 0`, `cmdport 0`), and supports NTS. |
| network | a DHCP client, [open](#open-questions); `dhcpcd` is the first candidate | all, except where the kernel command line configures the network | 1 | Must run non-root with only the capabilities listed below and no hook script, so no shell is needed (S1). qemu in M0 and M1 uses the kernel's `ip=` instead. |
| join | the join ([#5](https://github.com/GetFelix/ninelives/issues/5)) | all | 2, run once | Runs every boot and exits 0 at once if the machine has already joined, so the init needs no "first boot" logic. |
| felix-controlplane | Felix release | core | 3 | |
| felix-broker | Felix release | core, storage | 4 | Needs the control plane. |
| felixd | felixd release | all | 5 | Needs Felix ([felixd-contract.md](../felixd-contract.md#starting)). |

The firewall gets phase 0 because I6 orders services across phases only. A
role skips the phases it has no service in.

### The config

One file, `/usr/lib/ninelives/init.toml`, on the read-only root. The image
build writes it, and in M3 it is covered by the root's dm-verity hash like
every other file (S13). TOML because the repo already uses it for
`components.toml` and its parser is a small, widely used pure-Rust crate.
Unknown keys are errors, so a typo cannot silently drop a limit.

```toml
version = 1

[machine]
init_uid = 900
init_gid = 900
data_partition = "ninelives-data"   # GPT partition name, on the root's disk only
data_fstype = "ext4"                # decided in #4; needs project quotas
data_mount = "/var/lib/ninelives"
data_wait_s = 60
hostname = "ninelives"
watchdog_s = 60
shutdown_budget_s = 0               # 0 = no platform limit; set per target

[[mount]]                           # anything beyond the fixed early mounts
target = "/tmp"
fstype = "tmpfs"
options = "nosuid,nodev,noexec,size=64m,mode=1777"

[[service]]
name = "felixd"
roles = ["core", "storage", "worker"]
phase = 5
kind = "long-running"               # or "once"
exec = ["/usr/lib/felixd/felixd", "agent"]
uid = 1105
gid = 1105
state_dir = "felixd"                # $DATA/felixd, 0700, owned by uid
env = { FELIX_AUTH_TOKEN_FILE = "/var/lib/ninelives/credentials/felixd/token", FELIX_CA_FILE = "/var/lib/ninelives/credentials/felixd/ca.pem" }
join_env = ["FELIX_BROKERS", "FELIX_AUTH_TENANT", "FELIX_CONTROLPLANE_URL", "FELIX_SERVER_NAME", "FELIXD_NODE_ID"]
ambient = []                        # capabilities; non-empty only for firewall, time, network
seccomp = "/usr/lib/ninelives/seccomp/felixd.bpf"
landlock.read = ["/usr/lib", "/etc/ssl/certs", "/run/resolv", "/proc", "/var/lib/ninelives/credentials/felixd"]
landlock.execute = ["/usr/lib/felixd/felixd", "/usr/lib/x86_64-linux-gnu"]
landlock.write = []                 # felixd writes nothing locally
landlock.devices = ["/dev/null", "/dev/urandom"]
stop_timeout_s = 135
rlimits = { nofile = 65536 }
cgroup = { cpu_weight = 100, memory_min = "0", memory_low = "0", memory_max = "2G", io_weight = 100, pids_max = 1024 }
quota = "1G"                        # project quota on state_dir
```

What each part is for, so none of it is speculative:

- `uid` and `gid` are numbers. The init does not parse `/etc/passwd` or use
  NSS, and the image build checks that the numbers match the users it
  creates. No service uid or gid may be 0, `init_uid`, or another service's.
- `join_env` names values the join learns (brokers, tenant, control plane,
  node id) and writes to `$DATA/join/env`. They cannot be in the image. The
  init passes **only** the names listed here, and only to this service. See
  [Config integrity](#config-integrity) for why this is not config from
  writable state. `init check` requires `env` and `join_env` to be disjoint
  and rejects `LD_*` and `GLIBC_TUNABLES` in either.
- `seccomp` is a compiled BPF program built and reviewed at image build. The
  init loads it and never compiles a filter.
- `landlock` lists exactly what the program needs. Every path not listed,
  including `/dev`, `/sys/fs/cgroup`, `/run/ninelives` and the rest of
  `$DATA`, is denied. The profile test below runs the real program under its
  real profile, because a missing path shows only as a crash loop.
- `ambient` is the only way a service gets a capability, and only the three
  helpers have one ([What each service keeps](#what-each-service-keeps)).
- `quota` is an ext4 project quota on the state directory, so one service
  filling the disk cannot starve Felix or the init's records.
- `kind = "once"` is for the firewall and the join: each must exit 0 before
  the next phase starts.

CI runs `init check` (the same binary, not as PID 1) against every image's
config, so a malformed config fails the build rather than a boot.

### Kernel command line and arguments

The kernel names the init with `init=`. The init ignores its own `argv` and
environment when it runs as PID 1: the kernel passes unrecognised command
line words to init as arguments and environment, and nothing on the command
line may change what the init does. The image's command line sets
`panic=10` and `oops=panic`, so a kernel panic, including one caused by PID 1
dying, reboots the machine after 10 s with the panic on the console. In M3
the command line is part of the signed kernel image.

### Boot, as root

Everything in this section runs before the privilege drop, in this order,
and reads only the kernel, the read-only root and directory entries on
`$DATA` in directories no service can write.

1. Mount, failing closed on any error:

   | Target | Type | Options | Why |
   |---|---|---|---|
   | `/proc` | proc | `nosuid,nodev,noexec,hidepid=invisible,gid=nl-inspect` | Services cannot see each other's processes. The `nl-inspect` group has no members in production; the test image adds an inspector to it. |
   | `/sys` | sysfs | `nosuid,nodev,noexec` | |
   | `/dev` | devtmpfs | `nosuid,noexec` | The kernel may have mounted it already (`CONFIG_DEVTMPFS_MOUNT`); `EBUSY` with devtmpfs there is success. |
   | `/sys/fs/cgroup` | cgroup2 | `nosuid,nodev,noexec,nsdelegate,memory_recursiveprot` | |
   | `/run` | tmpfs | `nosuid,nodev,noexec,mode=0755,size=16m` | The root is read-only (S3). |

2. Read and check the config. It is on the read-only root and no service has
   run yet, so nothing untrusted can have written it. It has to be read as
   root because the mounts, directories and cgroups below come from it.
3. The config's `[[mount]]` entries.
4. The data partition: poll `/sys/class/block/*/uevent` for a partition
   whose `PARTNAME` matches `data_partition` **on the same disk as the root
   partition** (from M3, the disk under the dm-verity device), up to
   `data_wait_s`, and mount it at `data_mount` with
   `nosuid,nodev,noexec,prjquota`. Any other disk, including one attached
   later, is never considered, and there is no retry after the drop because
   the init can no longer mount. Two matches on the root's disk mean neither
   is mounted. Nothing on the data partition is executed: workload modules
   live in Felix.
5. Directories, each opened with `openat2(RESOLVE_NO_SYMLINKS |
   RESOLVE_BENEATH | RESOLVE_NO_XDEV)` and changed through the fd: `$DATA`
   (root, 0755), `$DATA/init` (init, 0700), `$DATA/join` (`nl-join`, group
   init, 0750), `$DATA/credentials` (root, 0755), and per service
   `$DATA/<state_dir>` (service, 0700) and `$DATA/credentials/<service>`
   (`nl-join`, group service, 0750). Each gets its project id and quota.
   Every parent here is root-owned, so no service can swap an entry the init
   opens, and the init changes only the directory itself, never what is in
   it. `/run/ninelives/init` (init, 0755) and `/run/resolv` (the network
   service, 0755) are created the same way.
6. The cgroup tree, every limit written, ownership set
   ([cgroups](#cgroups)), and the init moved into `ninelives/init`.
7. Kernel settings that must not change later: host name from `hostname`,
   loopback up, `reboot(LINUX_REBOOT_CMD_CAD_OFF)` so Ctrl-Alt-Del arrives
   as `SIGINT`, `kernel.core_pattern` to a fixed value compiled into the init
   (a file name, never a `|` pipe), `fs.suid_dumpable = 0`, and the hard
   `RLIMIT_CORE` to 0 and `RLIMIT_NOFILE` to the largest any service needs.
   Then `/proc/sys` is bind-remounted read-only, so no later process can
   change a sysctl, whatever capabilities it holds.
8. Open, close-on-exec: the console, `/dev/watchdog` if present, and each
   `/dev/input/event*` that reports `KEY_POWER`.
9. Fork the launchers ([Launchers](#launchers)), before any thread exists.
10. Start the worker thread, with every signal already blocked, then drop
    privilege ([Privilege](#privilege)).

The host name is the image's fixed one. The machine's identity is
`FELIXD_NODE_ID`; deriving the host name from the join's output would mean
reading service-written data as root.

### Launchers

A process that can start other processes under several uids can become any
uid, including 0, and with uid 0 it can rewrite root-owned files or point
`core_pattern` at a program of its choosing. So PID 1 never holds
`CAP_SETUID` or `CAP_SETGID` after boot. Instead, before the drop, it forks
one launcher per service, and each launcher:

1. sets its `oom_score_adj` to -1000, so the OOM killer never picks it over
   its service;
2. limits its bounding set to the service's `ambient` list;
3. `PR_SET_KEEPCAPS`, `setgroups([])`, `setresgid` and `setresuid` to the
   service's ids, then `capset` with permitted and inheritable equal to the
   `ambient` list and effective empty;
4. sets `PR_SET_DUMPABLE` to 0 and `PR_SET_NO_NEW_PRIVS`;
5. waits on its end of a `SOCK_SEQPACKET` socketpair PID 1 created.

A launcher is the same binary as the init, so nothing new ships. It stays in
`<service>/launcher` in the cgroup tree for the life of the machine and is
idle except while starting its service.

It applies no Landlock or seccomp of its own, because anything it restricts
its service inherits; its limits are the service's uid, capabilities and
cgroup, and its only input is PID 1's socket.

The protocol has fixed-size messages, at most 8 KiB:

| From | Message | Meaning |
|---|---|---|
| PID 1 | `start(generation, join_env values)` | Start one instance. PID 1 has already read and checked the values. |
| launcher | `started(generation, pid)` | The child passed `execve`. |
| launcher | `setup_failed(generation, step, errno)` | The child exited before `execve`. |

The launcher creates each instance with `clone3(CLONE_PARENT |
CLONE_INTO_CGROUP)`. `CLONE_PARENT` makes the service PID 1's child, not the
launcher's, so PID 1 gets its `SIGCHLD` and its exit status from the kernel,
not from a message, and PID 1's pid-reuse argument below still holds. PID 1
checks a reported pid with `waitid(P_PID, pid, WEXITED | WNOHANG | WNOWAIT)`,
which fails for anything that is not its child.

**A launcher that dies cannot be replaced**: re-forking it at the service's
uid would need `CAP_SETUID`. PID 1 sees it from `SIGCHLD` and the socket's
end of file, marks the service `failed: launcher lost` and the machine
`degraded`. A running instance keeps running, is reaped by PID 1, and is
still stopped at shutdown through `CAP_KILL` and `cgroup.kill`; it is just
never restarted. A reboot brings the launcher back. Nothing kills a launcher
but a bug, PID 1 at shutdown, or a process with its uid, and its service
cannot (see [Escalation](#escalation-through-the-init)).

### Privilege

PID 1's drop, after the launchers exist:

1. Limit the bounding set to `CAP_KILL` and `CAP_SYS_BOOT`.
2. `PR_SET_KEEPCAPS`, `setgroups([])`, `setresgid(init_gid × 3)`,
   `setresuid(init_uid × 3)`. The filesystem uids follow.
3. `capset`: permitted `CAP_KILL` and `CAP_SYS_BOOT`, inheritable and
   effective empty. Clear `PR_SET_KEEPCAPS`, set `PR_SET_DUMPABLE` to 0 so no
   process can ptrace PID 1 or read its memory through `/proc`.
4. `PR_SET_NO_NEW_PRIVS`.
5. Load PID 1's own seccomp filter, an allow-list of what its event loop and
   worker use. The `setuid` family, `setgroups`, `capset` raising anything,
   `mount` and the new mount API, `ptrace`, `bpf`, `kexec_load`,
   `init_module`, `quotactl` and `clone` are not on it; the launchers do the
   cloning.

| Kept | The operation that needs it |
|---|---|
| `CAP_KILL` | `SIGTERM` to a service's main process, which runs as another uid. A stop has to work even if that service's launcher is gone, so asking the launcher to send it is not enough. `SIGKILL` goes through `cgroup.kill`, which needs only write access to that file. |
| `CAP_SYS_BOOT` | `reboot(2)` at the end of shutdown. |

Both are in the permitted set only, raised to effective around the one call
and lowered straight after.

**Why nothing else.** `CAP_SETUID` and `CAP_SETGID` moved to the launchers,
which cannot use them because each has only its own service's ambient
list. `CAP_SYS_ADMIN` is not needed: every mount happens before the drop,
and shutdown uses `syncfs` instead of an unmount. Keeping it would make PID 1
root in all but name for the life of the machine, to save a journal replay
at the next mount.

What PID 1 could do if compromised after the drop: signal any process, kill
any service cgroup, and reboot. It cannot become uid 0 or a service's uid,
mount, change a sysctl (read-only `/proc/sys`), write a root-owned file,
load code into the kernel, or read raw disks (`/dev` block nodes are root
0600). The test plan checks that `setresuid(0)` from PID 1 fails.

**No root-owned file is writable after boot.** Outside `/proc`, `/sys` and
`/dev`, every root-owned file and directory is on the read-only root or was
created by the init before the drop with no write permission for any other
uid. The inspector test walks every writable mount and fails on a
root-owned file writable by anyone, and the image build fails on any setuid
or setgid file or file capability (S24).

**The residual.** From the kernel's `execve` of the init to the end of the
drop, the init is uid 0 with every capability. That window runs only the
boot steps above: mounts, the config parse, the data partition wait (at
most `data_wait_s`), directory and cgroup setup, a few sysctls, opening a
few devices and forking the launchers. Its inputs are the kernel, the
read-only root, sysfs, and directory entries in root-owned directories on
`$DATA`. No service has run in this boot, but earlier boots' services wrote
`$DATA`, which is why every open there refuses links and stays beneath its
root. An attacker who can rewrite the data partition offline reaches the
kernel's ext4 parser in this window; that is an
[open question](#open-questions) for the threat model.

### Signals and stop requests

The init blocks every signal before it starts its worker thread, so the
thread inherits the mask, reads them from a `signalfd`, and handles them in
its single-threaded event loop. Nothing runs in a signal handler.

| Source | Meaning | Action |
|---|---|---|
| `SIGCHLD` | a child exited | `waitpid(-1, WNOHANG)` until nothing is left. A pid that is a service's main process or a launcher updates that service; any other pid is an orphan, reaped and counted. |
| `SIGINT` | Ctrl-Alt-Del, from the kernel | ordered shutdown, then reboot |
| `SIGTERM` | reboot request | ordered shutdown, then reboot |
| `SIGUSR2` | power-off request | ordered shutdown, then power off |
| `KEY_POWER` press on an input device | the platform's power button: ACPI on x86_64 and on clouds, `gpio-keys` on aarch64 qemu | ordered shutdown, then power off |
| a byte on `/run/ninelives/init/request` | `r` reboot, `p` power off; anything else ignored | as above |
| any other signal | | queued in the `signalfd`, recorded and ignored |

The four signals have three meanings, following the sysvinit and busybox
convention. After the drop only the kernel can send them: no other process
has the init's uid or `CAP_KILL`. The request FIFO is owned by the init,
group `nl-stop`, mode 0620; the group has no members in production, so it
exists for a development image's unprivileged shell. Input events are read
as fixed-size `struct input_event` records and only `EV_KEY`, `KEY_POWER`,
value 1 counts.

The power button is part of handling a stop request (0002 item 5): it is how
qemu's `system_powerdown` and every cloud's stop reach a guest, and without
it every cloud stop in the M2 runs would be a hard power cut.

A request during shutdown changes only the final action, and only from
reboot to power-off, the more conservative of the two. There is no "force
now" request: every step is already bounded.

### Starting a service

PID 1 sends `start` to the service's launcher. The launcher is
single-threaded, and it builds everything the child needs (argv,
environment, the Landlock ruleset, the seccomp program, a close-on-exec
error pipe) before the clone, so the child only makes system calls. The
child is in the service's `run` cgroup from its first instruction, then:

1. Clears its signal mask and resets every disposition. The init's blocked
   mask reached the launcher, and a blocked mask survives `execve`, so
   skipping this would make the service deaf to `SIGTERM`.
2. `setsid`.
3. stdin from `/dev/null`, stdout and stderr to pipes whose read ends PID 1
   holds (passed to the launcher once, at fork), then
   `close_range(3, ~0, CLOSE_RANGE_CLOEXEC)`. That marks every other fd
   close-on-exec instead of closing it, so the Landlock fd and the error
   pipe survive until `execve`.
4. `chdir` to the state directory, sets the soft rlimits.
5. Raises each `ambient` capability into the ambient set. Across `execve` a
   non-root process without file capabilities keeps only its ambient set,
   so the service ends with exactly its list.
6. `landlock_restrict_self` with the service's ruleset, including
   `LANDLOCK_SCOPE_SIGNAL` and `LANDLOCK_SCOPE_ABSTRACT_UNIX_SOCKET`, so it
   can signal or connect to nothing outside its own domain, its launcher
   included. `no_new_privs` is inherited from the launcher.
7. Loads the service's seccomp filter, last, so it can forbid everything
   steps 1 to 6 used.
8. `execve` with an environment built from nothing: the config's `env`, then
   the `join_env` values.

Any failing step makes the child `_exit(127)` before `execve`, after writing
the step and errno to the error pipe. The launcher reports it, and the
service never runs with half its restrictions.

PID 1 sends `SIGTERM` to the main process to stop it, and kills by writing
`1` to the service's `run/cgroup.kill`, which takes every process in it,
including any the service forked.

### cgroups

```
/sys/fs/cgroup/                    controllers cpu memory io pids enabled
└── ninelives/                     memory.min and memory.low = sum of children
    ├── init/                      PID 1
    ├── system/
    │   └── firewall/ time/ network/ join/    one service group each
    ├── felix/                     the reservation for the log
    │   └── controlplane/ broker/
    └── felixd/
```

Each service group `<service>/` has two leaves, `launcher/` and `run/`, and
`memory pids` in its `cgroup.subtree_control`, which forbids processes in
`<service>/` itself. The limits sit on `<service>/`, so they cover the
service and its idle launcher together; `memory.oom.group = 1` sits on
`run/`, so an OOM kill takes the whole service and the init sees it exit.

Root writes every limit before the drop, and the files that control
placement and killing get exactly the owners that need them:

| File | Owner | Why |
|---|---|---|
| every limit file, and every directory | root | Nobody can change a limit or create a group after boot, the init included. |
| `<service>/cgroup.procs`, `run/cgroup.procs` | the service's uid | `CLONE_INTO_CGROUP` from `launcher/` to `run/` needs write access to the destination's and the common ancestor's `cgroup.procs`. |
| `launcher/cgroup.procs` | the init's uid | Nothing writes it after boot, so the service cannot move itself next to its launcher. |
| `run/cgroup.kill`, `<service>/cgroup.kill` | the init's uid | PID 1 kills a service, or at shutdown the whole group. |

A service can therefore only stay in `run/`: `<service>/` refuses
processes, `launcher/` is not writable to it, and leaving `<service>/` needs
`ninelives/` files that root owns.

A child's `memory.min` and `memory.low` protect only as much as every
ancestor's do, so `ninelives/` and `felix/` get the sums of their children.
Felix gets the reservation (`memory.min`, a high `cpu.weight`); that is
what N4 measures, and the numbers come from that measurement (N3), not from
this page. The image build checks that the sum of every `memory.max` leaves
room for the kernel and the init.

### Restart with backoff

- The n-th consecutive restart waits `min(1 s × 2^(n-1), 30 s)`, with ±25%
  jitter so a cluster-wide cold start does not have every felixd retry the
  same brokers at once. 1 s keeps the felixd-before-Felix race short. 30 s
  bounds how long a recovered dependency waits for its dependents to retry.
- The count resets once the service has run for 60 s, twice the cap, so a
  service that dies at the cap never looks healthy.
- After 5 consecutive restarts the service is reported `crash-looping`. It
  keeps being restarted.
- **Long-running services never give up.** A service usually fails because
  something else is down (Felix's quorum, the network), and it should come
  back when that does without an operator. A machine that stopped trying is
  useless and no easier to diagnose.
- **What gives up:** a start that fails for a reason a retry cannot change.
  The permanent errors are `ENOENT`, `EACCES`, `ENOEXEC` and `ELIBBAD` from
  `execve`, `EINVAL` or `EPERM` from Landlock, seccomp or raising an ambient
  capability (the config asked for something the kernel refuses), `ENOSYS`
  or `EOPNOTSUPP` from Landlock, and a lost launcher. The service is marked
  `failed`, the machine `degraded`, and the rest keep running. Every other
  error, `EAGAIN` and `ENOMEM` from `clone3` included, is retried with
  backoff: a wrong retry costs a few attempts, a wrong give-up costs the
  service until reboot.
- A `once` service that exits non-zero is retried with the same backoff,
  and the next phase waits. Its run is bounded by `run_timeout_s` (the join:
  300 s), after which it is killed and retried.
- A long-running service exiting 0 is treated as a failure, because nothing
  long-running should exit on its own.
- When a service's main process exits, PID 1 writes `run/cgroup.kill` at
  once, so a daemon it left behind cannot keep the cgroup alive. PID 1
  watches `run/cgroup.events` in its event loop, and asks for a new instance
  only after it reports `populated 0`. Two brokers on one data directory must
  never overlap. A cgroup still populated 5 s after the kill (a process in
  uninterruptible sleep) is reported `stuck`, and the restart waits for it.

### Ordered shutdown

On a request at time R, the init stops starting and restarting anything,
then stops phases from the highest to the lowest. Within a phase, services
stop in parallel. felixd goes first because it deletes its leases and node
entry in Felix, so Felix and the network must still be up; the network goes
last for the same reason.

For each service: `SIGTERM`, wait for exit up to `stop_timeout_s`, then
`run/cgroup.kill`, then wait up to 5 s for the cgroup to empty. A process
that cannot die is reported and shutdown goes on.

**The platform budget comes first.** Many platforms give a guest a fixed
time after the power button before cutting power. With a budget B, each
phase p gets a latest start time:

```
latest(p) = R + B - 35 s - sum over running phases q <= p of (largest stop_timeout_s in q + 5 s)
```

35 s is the final steps below. Phase p receives `SIGTERM` when every later
phase has finished, or at `latest(p)`, whichever is first; at that moment
anything still running in a later phase is killed. So Felix's deadline is
counted from the request, not from felixd's exit, and a felixd that ignores
`SIGTERM` cannot push Felix past the budget. **Felix wins over felixd**: cut
short, felixd's leases expire on their TTL elsewhere (about 10 s of delay),
while a Felix killed before it closes its logs means crash recovery, and
under `Periodic` or `None` fsync, lost acknowledged records. If B is too
small for even Felix's deadlines, every phase whose latest time has passed
starts at R.

Then the final steps:

1. Kill every service group, launchers included. An orphan stays in the
   cgroup of the service that created it, because cgroup membership survives
   reparenting, so this takes every orphan too; the init only reaps them.
2. Write the shutdown record (see below) and `fsync` it.
3. Close every fd the init holds on `$DATA`.
4. `syncfs($DATA)`, then `sync()`, bounded at 30 s together.
5. If `syncfs` returned 0, append a `synced` record, `fsync` it, close it.
6. The final record on the console, then raise `CAP_SYS_BOOT` and call
   `reboot(2)` with `RB_AUTOBOOT` or `RB_POWER_OFF`.

Steps 2 to 5 can block in the kernel. They run on the worker thread while the
main loop keeps petting the watchdog and enforcing the bounds; if a bound
passes, the init records it on the console and calls `reboot(2)` anyway. A
machine left half shut down is worse than one that looks like it lost
power, which Felix recovers from.

Why no unmount: once no process but the init has the filesystem open and
`syncfs` has returned 0, every write is on the device, and `syncfs` reports
writeback errors (Linux 5.8 and later). The ext4 journal replays committed
transactions at the next mount, which loses nothing. The unmount would add a
clean superblock flag and save that replay, at the price of
`CAP_SYS_ADMIN` in PID 1.

| Service | Deadline | Why |
|---|---|---|
| felixd | 135 s | felixd's worst case is about `2 s × 64 workloads + 2 s` = 130 s ([felixd-contract.md](../felixd-contract.md#stopping)), plus 5 s for the process to exit. |
| felix-broker | 30 s, proposed | Felix documents no stop bound. Open question. |
| felix-controlplane | 30 s, proposed | Same. |
| join, network, time | 5 s each | They hold no state that must be written. |

With no platform budget the worst case from request to `reboot(2)` is the
deadline plus the 5 s kill wait of each phase with a running service, plus
the final steps: felixd 140, broker 35, control plane 35, join 10 (first
boot only), time and network 10, final 35, so 265 s.

| Platform | Budget B | Source |
|---|---|---|
| qemu in CI | none; the harness waits for power-off | ours |
| Each cloud target | measured before that target's first M2 run | open question |
| Bare metal | none, unless the operator's tooling sets one | |

### How shutdown shows Felix flushed

What the init can establish, in order:

1. Each Felix service exited on its own with status 0 after `SIGTERM`,
   before its deadline, rather than being killed.
2. No Felix process remains: the service's cgroup is empty.
3. `syncfs` on `$DATA` returned 0 after that.

Once a Felix process is gone, everything it wrote is in the kernel; Felix's
own [durability docs](https://github.com/GetFelix/felix/blob/v0.7.2/docs/durable-storage.md#durability-policies)
note that even `FsyncMode::None` data survives a process crash because the
page cache belongs to the kernel. Steps 2 and 3 move that to the device. So
the chain holds if and only if Felix, on `SIGTERM`, writes everything it has
acknowledged and closes its logs before exiting 0. Felix does not document
that today, so step 1 is evidence, not proof. Closing that gap needs a
Felix contract page like [felixd-contract.md](../felixd-contract.md); see
[Open questions](#open-questions).

The independent check is end to end. A test publisher writes records to a
Felix stream configured with `Periodic` fsync, the widest loss window, and
keeps every acknowledged offset; the harness requests power-off while it is
still publishing. After the next boot a consumer reads the stream back, and
the test fails if any acknowledged record is missing. A check that only
looks for a torn tail would pass even with the sync skipped, because ext4
can drop unsynced appends without leaving one. Felix's own recovery plan
(`felixctl data inspect segments`, run from outside the VM on the powered-off
disk so the tool stays off the image, S1) is a second check that nothing
needed repair.

The shutdown record lists, per service, `exited(status)` or
`killed-at-deadline`, and the time taken. On the next boot the init reads the
previous boot's records and reports `previous_shutdown`:

- `clean`: a shutdown record in which every service exited on its own,
  followed by `synced`.
- `forced`: a shutdown record, but a service was killed, a bound passed, or
  `synced` is missing.
- `unknown`: no shutdown record (power was lost). A torn last line counts as
  missing.

### Watchdog and panics

- If `/dev/watchdog` exists, the init sets `watchdog_s` (60 s) and pets it
  every 10 s from the main loop, through shutdown, up to `reboot(2)`. If the
  loop hangs, the machine resets. The kernel config builds in `softdog` as a
  fallback where there is no hardware watchdog (qemu has i6300esb). The
  watchdog proves the init's loop runs, nothing about services.
- The init is built with `panic = "abort"`. A panic hook writes the message
  to the console, then the process aborts, the kernel panics ("Attempted to
  kill init") and reboots after 10 s. The code rules
  ([0005](../decisions/0005-rust-and-unsafe.md)) are what keep panics out.

### State reports

Records are JSON lines, one per state change, each with a format version,
`CLOCK_MONOTONIC` and wall time, and a boot id. Machine states: `booting`,
`up`, `degraded`, `emergency`, `stopping`. Service states: `starting`,
`running`, `exited`, `backoff`, `crash-looping`, `failed`, `stuck`,
`stopping`, `stopped`, `killed-at-deadline`.

| Where | When | Why |
|---|---|---|
| `/dev/console`, prefixed `ninelives-init ` | from the first instruction | The only channel before `/run` and `$DATA` exist, and what CI reads on the serial port. The `up` record is the ready line #2 waits for. |
| `/run/ninelives/init/state.json` | after `/run` is mounted | A snapshot of current state, replaced with write-and-rename, readable by the diagnostics service (S11). Includes the longest main loop iteration so far. |
| `$DATA/init/records.jsonl` | after `$DATA` is mounted | The history that survives a reboot (S11). Two files of 4 MiB, rotated. Records from before the mount wait in a 1024-record ring. |

Each service's stdout and stderr go to `$DATA/init/output/<service>`, two
files of 16 MiB each, rotated, written by the init. Before `$DATA` is
mounted, output is held in a 64 KiB buffer per service. Service output never
goes to the console, because a busy service would drown the console that
shows why a boot failed.

The console is opened non-blocking. When the console, the disk or a pipe
cannot keep up, the init drops what does not fit and counts it per sink, and
the next record that gets through carries the count. It always keeps
reading the pipes, because a Felix broker blocked on writing a log line is a
stalled log.

The init's records and output have their own project quota on `$DATA/init`.
The filesystem's reserved blocks belong to the init's uid (set when #4
creates the filesystem), so records still land when a service has filled
everything else, and the fill is visible.

None of this needs Felix, which is how state is reported before Felix is
up. Publishing records to Felix is M4.

### Implementation constraints

- One thread runs the event loop (`signalfd`, the pipes, the launcher
  sockets, the power button, the request FIFO, each service's
  `cgroup.events`, a timeout from the nearest deadline). One worker thread
  runs the calls that can block in the kernel (`syncfs`, `sync`, `fsync`),
  started after the launchers are forked and the signal mask is set.
- Deadlines use `CLOCK_MONOTONIC`. A clock step from time sync changes no
  decision.
- The decision logic is a pure state machine over events (signal, child
  exit, launcher message, timer, output, cgroup event) and returns actions.
  The system call layer executes them. This is what makes most invariants
  testable without a VM.
- `unsafe` lives in `mod sys` ([0005](../decisions/0005-rust-and-unsafe.md)):
  `clone3` and the child's setup, the seccomp loads, `capset`, and whatever
  rustix does not cover. Dependencies: rustix, serde and toml for the
  config, serde_json for the records. Each one gets its sentence in the pull
  request that adds it.

## Invariants

- **I1.** The init never calls `exit` and never returns from `main`. Its only
  way out is `reboot(2)`.
- **I2.** Every `SIGCHLD` the loop reads is followed in the same iteration by
  `waitpid` until no exited child is left, and no zombie child of PID 1 lives
  longer than 100 ms on a machine that is not overloaded.
- **I3.** Every service process runs, from its first instruction, in its
  service's `run` cgroup, with its configured uid and gid and no
  supplementary groups, and from `execve` on with permitted, effective and
  ambient sets equal to its `ambient` list, `no_new_privs`, its Landlock
  ruleset with signal and socket scoping, and its seccomp filter. A child
  whose setup fails at any step never reaches `execve`.
- **I4.** At most one instance of a service exists. A new one starts only
  after the old one's `run` cgroup reports `populated 0`.
- **I5.** The n-th consecutive restart of a service waits within ±25% of
  `min(2^(n-1), 30)` seconds, and the count resets only after 60 s of
  continuous running.
- **I6.** No service in phase p starts before every long-running service in
  earlier phases has been started and every `once` service in them has
  exited 0.
- **I7.** After a stop request no service is started or restarted.
- **I8.** A service in phase p receives `SIGTERM` only once every service in
  later phases has exited or been killed, and no later than `latest(p)`.
- **I9.** A service is killed no earlier than its `stop_timeout_s` after its
  `SIGTERM` unless an earlier phase's `latest()` forces it, and the init
  reaches `reboot(2)` within the platform budget, or within 265 s without
  one.
- **I10.** `reboot(2)` follows a `syncfs` attempt on `$DATA` made after every
  service cgroup was empty or reported stuck, except where a bound passed,
  which is recorded on the console first.
- **I11.** A service's environment contains exactly its `env` and its
  listed `join_env` names, and it inherits no file descriptor besides 0, 1
  and 2.
- **I12.** What the init runs, as whom, with what privilege, is decided only
  by the config on the read-only root. Nothing on `$DATA`, the command line,
  a signal, a launcher message or the request FIFO changes it. A missing or
  malformed config starts no service.
- **I13.** The init never opens a file under `$DATA/credentials/`, and no
  record or output it writes contains a value from `join_env`.
- **I14.** No main loop iteration waits on the console, a service pipe or the
  data partition; the longest iteration stays under 100 ms. What does not fit
  is dropped and counted.
- **I15.** The init's files on `$DATA` never exceed 8 MiB of records plus
  32 MiB of output per service, and no service writes more than its quota.
- **I16.** Every state change of the machine or a service produces one
  record on each sink, or is counted as dropped on that sink. Records that
  reach a sink are in order.
- **I17.** After the drop no process on the machine, PID 1 included, has a
  real, effective, saved or filesystem uid of 0. PID 1's permitted set is
  exactly `CAP_KILL` and `CAP_SYS_BOOT`, and its effective set is empty
  outside the two calls that use them. Each launcher and service holds only
  its `ambient` list.
- **I18.** No service starts unless every step of PID 1's drop and its
  launcher's drop succeeded.
- **I19.** After boot, no sysctl changes, and no root-owned file outside
  `/proc`, `/sys` and `/dev` is writable.

## Failure modes

PID 1 exiting panics the kernel, which reboots after 10 s. The rows marked
"no" end that way or in a reset; every other row leaves the init running.

| Failure | Detection | Action | Survivable |
|---|---|---|---|
| A service crashes or exits | `SIGCHLD`, `waitpid` | Kill its `run` cgroup, restart with backoff; `crash-looping` after 5 | yes |
| A service hangs without exiting | Not detected | Nothing until a stop request (non-goal: health checks). felixd's leases still expire elsewhere if it stops renewing. | yes |
| A service ignores `SIGTERM` | Deadline or `latest()` passes | `cgroup.kill`, recorded as `killed-at-deadline`, shutdown continues | yes |
| A service process stuck in uninterruptible sleep | cgroup populated 5 s after kill | `stuck`; restart waits; at shutdown, recorded and `reboot(2)` ends it | yes |
| A service leaves a daemon or forks a bomb | `cgroup.events`; `pids.max` | Contained by `pids.max`; all of it killed with the main process | yes |
| A service floods its output | Rotation and drop counters | Rotated, then dropped and counted; the service never blocks | yes |
| A service exhausts memory | `memory.max`, `memory.oom.group` on `run/` | The kernel kills the whole service, never the launcher, and the service restarts | yes |
| A service fills the disk | Its project quota | It gets `EDQUOT`; Felix and the init's records keep their space | yes |
| A launcher dies | `SIGCHLD`, socket end of file | `failed: launcher lost`, machine `degraded`; a running instance is left to run and still stopped at shutdown; no restart until reboot | yes, without that service |
| A launcher sends a malformed or unexpected message | Fixed sizes, generation numbers, `waitid` check of the pid | Ignored and recorded; the service's state follows the kernel's `SIGCHLD` and `cgroup.events`, not the message | yes |
| A permanent setup error (binary, Landlock, seccomp, ambient raise) | `setup_failed` | `failed`, machine `degraded`, others keep running | yes |
| A transient start error (`EAGAIN`, `ENOMEM`) | `setup_failed` or `clone3` error | Retried with backoff | yes |
| felixd starts before Felix answers | felixd exits 1 | Ordinary restart with backoff | yes |
| The join never succeeds | Non-zero exits or `run_timeout_s` | Retried forever with backoff; role services never start; reported | yes |
| A fixed early mount fails | `mount` error | `emergency`: the drop still happens, no service starts, the error repeats on the console every 30 s, stop requests and reaping still work | yes, does nothing else |
| Config missing, unreadable or malformed | Read or parse error | Fail closed: `emergency`, as above. Never a default config. | yes, does nothing else |
| **Any step of PID 1's drop fails** | The call's error | `emergency`: no `start` is ever sent, and every launcher is killed so nothing can start under a service uid. PID 1 then only reaps, pets the watchdog, handles stop requests and repeats the failure on the console. Staying up but idle is chosen over rebooting because a broken drop would loop, and over continuing because S24 forbids a running machine with a root PID 1. | yes, does nothing else |
| A launcher's drop fails | It exits before its socket is ready | That service `failed`, machine `degraded` | yes, without that service |
| Data partition missing, duplicated or unmountable | Not found within `data_wait_s`, two matches, `mount` error | `degraded`: firewall, time and network run, nothing else; never formatted; no retry, a reboot retries | yes |
| Data partition full despite quotas | Write errors | Dropped and counted on the console | yes |
| Console blocked or absent | `EAGAIN`, open failure | Dropped and counted; `/dev/kmsg` used if the console cannot be opened | yes |
| Disk hangs during shutdown | `syncfs` bound passes | Recorded on the console, `reboot(2)` anyway; `previous_shutdown` reads `forced` | yes |
| Power cut during shutdown | Next boot reads `previous_shutdown: unknown` | Felix recovers as after any crash | yes (the next boot) |
| Platform cuts power before the budget we assumed | Same as a power cut | Same; the budget table is wrong and is fixed | yes (the next boot) |
| Clock stepped by time sync | Not needed | Deadlines are monotonic | yes |
| The init's loop hangs (a bug) | Watchdog not petted | Hardware or softdog reset after 60 s | no, reset |
| The init panics | Panic hook | Message on the console, abort, kernel panic, reboot after 10 s | no |
| An allocation in the init fails | Rust aborts | As a panic. The init's memory is bounded by its fixed buffers, and the kernel never OOM-kills PID 1. | no |
| Kernel oops | `oops=panic` | Reboot after 10 s | no |
| Hostile `$DATA/join/env`: unknown names, `LD_PRELOAD`, huge file, binary bytes, a symlink | `O_NOFOLLOW`, 4 KiB cap, per-service allowlist, value checks | Values must be printable ASCII under 1 KiB; `LD_*` and `GLIBC_TUNABLES` are refused even if listed; anything else rejects the whole file, the service does not start, `degraded` | yes |
| Hostile symlink planted in a state directory | Not followed: the init opens only the directory itself, beneath root-owned parents, without following links | Nothing outside is touched | yes |
| A service signals or ptraces PID 1, its launcher or another service | Kernel permission checks, Landlock scoping, non-dumpable launchers and PID 1 | Refused | yes |
| A service writes the request FIFO | File permissions | Refused: not in `nl-stop` | yes |
| A service tries to leave `run/`, join its launcher's group or change a limit | File ownership and the no-internal-process rule | Refused | yes |
| A service changes its directory's project id to escape its quota | Its seccomp filter | `FS_IOC_FSSETXATTR` is refused | yes |
| PID 1, compromised, tries `setresuid(0)`, `mount` or a sysctl write | No capability, its own seccomp filter, read-only `/proc/sys` | Refused | yes |
| Hostile words on the kernel command line | Ignored | The init reads neither `argv` nor its environment as PID 1 | yes |
| A disk attached later, or a second disk, with a partition named `ninelives-data` | Only the root's disk is searched, only before the drop | Never mounted | yes |
| Malformed input events | Fixed-size reads, type and code check | Ignored | yes |

## Security

The threat model ([#56](https://github.com/GetFelix/ninelives/issues/56)) is
not written yet. This section states what it assumes, and each assumption
is listed under [Open questions](#open-questions). A NineLives machine is a
node in a cluster, so one compromised machine is a foothold into it, and one
compromised service must stay one compromised service.

### What the init runs as and can reach

uid 0 only until the drop ([Privilege](#privilege)), then its own uid with
`CAP_KILL` and `CAP_SYS_BOOT` permitted and nothing effective except around
their one call. No process on the machine is root after that (I17, S24).
PID 1 opens no socket to anything but its launchers, listens on nothing and
makes no network connection.

### Every input it reads

| Input | Who can write it | Validation |
|---|---|---|
| `/usr/lib/ninelives/init.toml` | The image build only. The root is read-only (S3), and from M3 dm-verity checked against a signed kernel image (S13). | Parsed before the drop, when no service has run; unknown keys rejected, size capped, checked at image build. Missing or malformed: fail closed. |
| Kernel command line, `argv`, environment | The bootloader; signed from M3 | Ignored as PID 1. `panic=` and `oops=` are the kernel's. |
| Signals | The kernel; nobody else after the drop | Four signals, three meanings, all of them stop or reap. |
| Child exit status | The kernel, from `waitpid` | A pid that is not a service's main process or a launcher is an orphan, reaped and counted. |
| Launcher messages | A launcher, at its service's uid | Fixed-size, generation-checked; pids checked with `waitid`; state follows the kernel's events, so a lying launcher can only fail its own service. |
| Service stdout and stderr | The service | Opaque bytes: stored, size bounded, never parsed or acted on. |
| `/sys/class/block/*/uevent` | The kernel, from partition tables anyone with the disk can write | Only `PARTNAME` on the root's disk, exact match, duplicates refused, before the drop only. |
| `cgroup.events` | The kernel | Only `populated`. |
| Power button input devices | The kernel, from the platform | Fixed-size events, only `KEY_POWER` press. |
| `/run/ninelives/init/request` | The init and `nl-stop` (empty in production) | One byte, `r` or `p`, else ignored. |
| `$DATA/join/env`, `$DATA/join/role` | The join | Read after the drop. Never follows links, capped, allowlisted, see the failure table. An unknown role starts no role service. |
| `$DATA/init/records.jsonl` (previous boot) | Only the init | Read after the drop, to report `previous_shutdown`; a line that does not parse counts as missing. |
| Directory entries on `$DATA` | Earlier boots' services, inside their own directories only | Opened before the drop beneath root-owned parents with `RESOLVE_NO_SYMLINKS \| RESOLVE_BENEATH \| RESOLVE_NO_XDEV`. |

### Config integrity

What runs, as whom and with what privilege comes only from the image (I12).
`$DATA/join/env` and `$DATA/join/role` are the only inputs from writable
state, and they are data, not config: they choose which of the image's role
lists applies and supply values for names the image already chose, for the
services the image named. They cannot add a service, change a uid, a
capability, a filter or a path. A compromised join could point felixd at
hostile brokers, but it could do that anyway through the credentials it
writes; the init adds no new power. Removing even this path needs felixd to
read these settings from a file itself, which 0.1.0 does not do.

### What each service keeps

These are the per-service capabilities S6 lists. A launcher holds the same
set as its service, in permitted and inheritable only.

| Service | uid | Capabilities | Why |
|---|---|---|---|
| firewall | own | `CAP_NET_ADMIN`, ambient | Loading the packet filter needs it. It runs once and exits. |
| time (chronyd) | `_chrony` | `CAP_SYS_TIME`, ambient | Setting the clock has no unprivileged path. chronyd's `-U` lets it start non-root. |
| network (DHCP client) | own | `CAP_NET_ADMIN`, `CAP_NET_RAW`, `CAP_NET_BIND_SERVICE`, ambient | Configuring addresses and routes needs `CAP_NET_ADMIN`, sending before it has an address needs a raw socket, and the client port is 68. |
| join | `nl-join` | none | |
| felix-controlplane, felix-broker | own each | none | Their ports are above 1024. |
| felixd | own | none | [felixd-contract.md](../felixd-contract.md#running-it): needs no root and no capabilities. |

`CAP_NET_ADMIN` in the DHCP client is the broadest grant here: a compromised
client could also change the packet filter. Its seccomp filter and Landlock
profile are the tightest of any service for that reason, and a narrower
split is an [open question](#open-questions). No service gets
`CAP_SYS_ADMIN`, `CAP_SYS_PTRACE`, `CAP_SYS_MODULE`, `CAP_DAC_OVERRIDE`,
`CAP_SETUID`, `CAP_SETGID` or `CAP_CHOWN`. Every service also gets
`no_new_privs`, its seccomp filter, its Landlock ruleset with signal and
socket scoping, its cgroup limits and quota, `/proc` with
`hidepid=invisible`, and a read-only root (S6). io_uring, which bypasses
seccomp, is compiled out of the kernel. The image has no setuid or setgid
file and no file capabilities, and the image build fails if one appears
(S24).

### Escalation through the init

- **Signals and ptrace.** A service cannot signal or ptrace PID 1, another
  service, or its own launcher: PID 1 and the other services have other
  uids and it has no `CAP_KILL`, its launcher is outside its Landlock domain
  (which scopes signals and, in every Landlock domain, ptrace), and PID 1
  and the launchers are not dumpable.
- **Pid reuse.** PID 1 signals only its own direct children by pid (every
  service is one, through `CLONE_PARENT`), and only before reaping them. A
  pid cannot be reused until its parent reaps it, and PID 1 is that parent,
  so a signal can never hit a recycled pid. That is why PID 1 needs no
  pidfds. Everything beyond the main process is killed through
  `cgroup.kill`, which names the cgroup, not a pid.
- **cgroups.** See the ownership table: a service can only stay in `run/`,
  and no one can change a limit.
- **The launcher.** Its only input is PID 1's socket; a compromised service
  cannot write to it (Landlock's abstract socket scoping does not apply to a
  socketpair, but the service holds no fd to it, since the child's fds are
  close-on-exec). A compromised launcher has its service's uid and
  capabilities and nothing more.
- **PID 1 itself.** No `CAP_SETUID`, `CAP_SETGID` or `CAP_SYS_ADMIN`, not
  dumpable, an allow-list seccomp filter, read-only `/proc/sys`, and a fixed
  `core_pattern`, so even a compromised PID 1 has no path back to uid 0.
- **File descriptors.** Every init and launcher fd is `O_CLOEXEC`, and the
  child marks everything above 2 close-on-exec (I11).
- **Environment.** Built from nothing per service (I11).
- **Files the init writes.** `$DATA/init` and `/run/ninelives/init` are the
  init's, mode 0700 and 0755, which no service can write.

### Secrets

The init never reads credentials and never holds one (I13, S8). Each
service's credential directory, `$DATA/credentials/<service>/`, is owned by
`nl-join` with the service's group, mode 0750. The join writes each file
there mode 0640 with write-and-rename, so it needs no capability, the
service reads it through its group, and no other service can. felixd gets
only paths, `FELIX_AUTH_TOKEN_FILE` and `FELIX_CA_FILE`, from the image
config, and reads the token itself on each use
([felixd-contract.md](../felixd-contract.md#running-it)). So the token never
passes through the init, a launcher message, a command line or an
environment. Service output may contain whatever a service prints; keeping
secrets out of it is each service's obligation under S8, and the S8 test
checks it.

### Requirements

S3 (the init writes only `/run` and `$DATA`), S6 (the start sequence and the
per-service capability table), S7 (chronyd opens no port; the DHCP client's
port 68 goes in the image's port list), S8 (secrets), S11 in part (state and
output kept on `$DATA` with a size bound; the authenticated interface that
reads them and kernel crash dumps are separate), S24 (no root after the
drop), N3 and N4 (cgroup reservation, numbers from measurement).

## Test plan

Unit tests run the state machine with a fake clock and fake events, and
cover most invariants without a kernel. qemu tests boot the CI image
([#2](https://github.com/GetFelix/ninelives/issues/2)); tests that inspect
processes add an `nl-inspect` service with no capabilities, which the
production image lacks. Tests marked "test image" also add a service with
`CAP_KILL`, or build the init with a fault hook that production builds do
not contain. Fault tests run on the schedule in
[#50](https://github.com/GetFelix/ninelives/issues/50).

| Test | Kind | Proves |
|---|---|---|
| Event sequences never produce an exit action; every error path returns to the loop | unit, property | I1 |
| A static check that `std::process::exit` and a returning `main` appear only in the `init check` and local paths | CI | I1 |
| A `SIGCHLD` event always yields `waitpid` until empty in the same step | unit | I2 |
| A test service makes 1000 children that exit; the inspector polls `/proc` every 10 ms and sees no zombie of PID 1 older than 100 ms | qemu | I2 |
| Every process's `/proc/<pid>/status` (Uid, Gid with all four ids, Groups, CapPrm, CapEff, CapInh, CapAmb, NoNewPrivs, Seccomp) and `/proc/<pid>/cgroup` match the config and the capability tables, PID 1 and launchers included | qemu | I3, I17, S6, S24 |
| A fault hook makes PID 1 call `setresuid(0, 0, 0)`, `setresuid` to a service uid, and `mount` after the drop; each fails and the init keeps running | qemu (test image) | I17 |
| Fault injected at each child setup step: the service never execs, the step and errno are reported | unit (sys mocked), qemu | I3, permanent and transient errors |
| Each real service runs under its real Landlock and seccomp profile against a single-node cluster in the image and stays `running` for 5 minutes; felixd appears in `nodes` | qemu | profiles are complete |
| A service exits leaving a daemon; the daemon is killed and the next instance starts after `populated 0` | qemu | I4 |
| A test service tries to move itself into `<service>/`, `launcher/` and another service's group | qemu | cgroup ownership |
| A launcher is killed (test image): its service is `failed: launcher lost`, the running instance is stopped at shutdown | qemu (test image) | launcher row |
| Backoff schedule, jitter bounds and the 60 s reset | unit | I5 |
| Phase order with a `once` service that fails twice | unit, qemu | I6 |
| A service crashes during shutdown and is not restarted | unit, qemu | I7 |
| Shutdown order and `SIGTERM` times against the config, with and without a budget; a felixd that ignores `SIGTERM` does not delay the broker's `SIGTERM` past `latest()` | unit, qemu | I8 |
| A service that ignores `SIGTERM` is killed no earlier than its deadline, and power-off lands within 265 s, or within B | qemu, fault | I9, #48 |
| Power-off with the data disk throttled to zero: `reboot(2)` after the bounds, next boot `forced` | fault | I10 |
| A test service dumps its environment and `/proc/self/fd` | qemu | I11 |
| `$DATA/join/env` planted with `LD_PRELOAD`, `GLIBC_TUNABLES`, a symlink, an oversized file and binary bytes | qemu | I12, hostile inputs |
| `init check` rejects overlapping `env` and `join_env`, `LD_*`, and uid 0 or a shared uid | unit | I12 |
| A symlink planted inside a state directory pointing at `/run/ninelives`; nothing outside changes | qemu | hostile inputs |
| Known test credentials planted; console, records, output and `/run` searched | qemu | I13, S8 |
| A service writes 1 GB to stdout while the console is throttled; `state.json` reports the longest iteration under 100 ms, and sinks returning `EAGAIN` never block the fake loop | qemu, unit | I14 |
| A service fills its quota and keeps writing; the init's records and Felix's directory still accept writes | fault | I15, disk full |
| Every transition in the unit traces yields a record or a drop count per sink; console order matches | unit, qemu | I16 |
| Image build scan for setuid, setgid and `security.capability` files, seeded with one to see it fail | CI | S24 |
| Each drop step, PID 1's and a launcher's, made to fail (fault hook): no service starts under that failure, `emergency` or `failed` on the console, power-off still works | fault (test image) | I18 |
| The inspector writes every sysctl and walks every writable mount for root-owned writable files | qemu | I19 |
| Services send every signal to PID 1, their launcher and each other, try `ptrace` on each, and write the request FIFO and cgroup files: all refused | qemu | escalation |
| A test-image service with `CAP_KILL` sends every signal but the four to PID 1, `SIGABRT` and `SIGSEGV` included; nothing changes | qemu (test image) | signal table |
| A fault hook panics the init: the message is on the console and the VM reboots within 15 s | fault (test image) | panic row |
| The init loop made to hang (fault hook): the watchdog resets the VM | fault (test image) | watchdog row |
| Kernel command line with extra words and `KEY=VALUE` pairs boots identically | qemu | hostile inputs |
| A second disk, and a disk hot-attached after boot, with a partition named `ninelives-data`: never mounted | qemu | hostile inputs |
| Malformed and missing config: the build fails (`init check`); booted anyway, `emergency`, power-off still works | CI, qemu | fail closed |
| qemu `system_powerdown` on x86_64 and aarch64 produces an ordered shutdown | qemu | power button |
| Acknowledged records written under `Periodic` fsync up to the power-off request are all read back after the next boot; `felixctl data inspect segments` on the disk reports nothing to repair; `previous_shutdown: clean` | qemu | 0002's durability check |
| Power cut during shutdown, then boot: `previous_shutdown: unknown`, Felix starts | fault | power loss row |

## Open questions

| Question | Proposal | Decides | Needed by |
|---|---|---|---|
| **Felix's stop contract.** What Felix's broker and control plane guarantee on `SIGTERM` (everything acknowledged written and logs closed before exit 0, in every fsync mode, including the control plane's Raft store), and their worst-case stop time. Without it, a clean exit is evidence and the end-to-end test is the proof. | A `docs/felix-contract.md` page, like felixd's, backed by Felix docs or an issue filed in Felix; deadlines set from it | NineLives maintainers with Felix | M1 (#48) |
| Whether `felixctl` ships in the Felix release tarball, and whether anything inspects the control plane's store the same way | Check 0.7.2's artifact; file a Felix issue for what is missing | NineLives maintainers | M1 |
| A readiness signal from Felix, so felixd's early exits are not counted as restarts | Live without it; revisit if boot time suffers | NineLives maintainers | M2 |
| **Platform stop budgets.** How long each cloud waits after the power button before cutting power | Measure each target and fill the budget table; a target whose budget cannot cover Felix's deadlines is reported, not shipped silently | NineLives maintainers | Each cloud target's first M2 run |
| **Which DHCP client.** It must run non-root with only the three ambient capabilities and no hook script. dhcpcd's privilege separation expects to start as root; whether it runs this way with privilege separation off is unverified. | Try dhcpcd in qemu; if it cannot, a small DHCP client in Rust as a separate service (not PID 1). Either way, splitting the address change from the DHCP exchange would narrow `CAP_NET_ADMIN`. | Network design | M1 |
| DNS: something must write `/run/resolv/resolv.conf` without a shell hook | The DHCP client writes it directly, or the join records brokers by address | Network design, #5 | M1 |
| Which program loads the packet filter (S7) | A static nftables ruleset from the image, loaded by a minimal loader in phase 0 | Network design | M1 |
| How the seccomp and Landlock profiles are produced and reviewed | Recorded from qemu runs, minimised by hand, compiled at image build, reviewed on each component bump | Image design | M1 |
| **Authenticated time.** Clouds' metadata NTP servers do not offer NTS. Without it an on-path attacker can step the clock: certificates then fail (forward) or expired ones pass (back). chronyd is limited to stepping during its first three updates (`makestep 1 3`) and refuses offsets over 1000 s after that (`maxchange`). Whether Felix and felixd leases depend on wall time is not documented. | NTS where the servers support it; otherwise the limits above, and the threat model decides | Threat model (#56) | M1 |
| Whether Felix waits for time sync, since certificate checks need a sane clock | No wait; qemu and clouds have an RTC | Threat model (#56) | M1 |
| **Data partition integrity.** Anyone who can rewrite the disk offline reaches the kernel's ext4 parser while the init is still root, and can plant or alter Felix's data and credentials. Integrity or encryption sealed to a TPM or vTPM would close that. | Treat it as a likely requirement; #56 decides, with M3's verified boot | Threat model (#56) | M3 at the latest |
| **Threat model assumptions** this page makes: the image is trusted once built and, from M3, verified; the data partition is not trusted for what runs but is trusted against offline tampering until the question above is settled; physical access is out of scope until M3; a compromised service is the main attacker. | Confirm or correct in #56 | Threat model (#56) | M1 |
| Whether `emergency` should reboot so M3's boot counting can fall back to the other image | Stay up and report until M3; M3 decides | A/B design (#8) | M3 |
| cgroup numbers (`memory.min`, `cpu.weight`) and quotas | From the N4 measurement | NineLives maintainers | M2 |
| Data filesystem features: ext4 with `quota` and `project`, reserved blocks owned by the init's uid | Adopt in #4 | Data partition design (#4) | M1 |
