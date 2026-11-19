# init (PID 1)

The scope is fixed by [0002](../decisions/0002-our-own-init.md). This page
says how the init meets it. The first slice,
[#47](https://github.com/GetFelix/ninelives/issues/47) (M0), is in
[`init/`](../../init/); its README lists which parts of this page it
implements. The services and shutdown are
[#48](https://github.com/GetFelix/ninelives/issues/48) (M1).

Paths below use `$DATA` for the data partition's mount point, which the
image config names. Requirement S24 (no root after early boot) and the
per-service capability list in S6 are being added with the threat model
([#56](https://github.com/GetFelix/ninelives/issues/56)); this page cites
them by those numbers.

## What it does

The init is the first process the kernel starts. While it still has the
uid 0 the kernel gave it, it mounts the kernel filesystems and the data
partition, builds the cgroup tree, creates the directories services need,
forks one launcher per service, each of which drops at once to that
service's uid, and has the firewall's launcher load the packet filter.
Then the init drops to its own unprivileged user for good, keeping only
`CAP_KILL` and `CAP_SYS_BOOT`, before any service runs. From then on no
process on the machine is root (S24). It reaps every orphan, has the
launchers start the services the machine's role needs in a fixed order
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
  is failing, or changes the machine's role. The one reboot it makes on its
  own follows a lost launcher ([Launchers](#launchers)), because without a
  launcher that service can never start again.
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
- **No network configuration beyond loopback.** DHCP and time sync are
  services, and a separate loader sets the packet filter before the drop.
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
| firewall | the packet filter loader, [open](#open-questions) | all | 0, run once, before the drop | Loads the S7 ruleset before any interface is configured, so no packet arrives unfiltered, and before the drop, so no running service needs netfilter access. |
| time | `chronyd` (Debian `chrony`) | all | 1 | Runs in the foreground (`-d`), starts non-root with only `CAP_SYS_TIME` (`-U`), ships its own seccomp filter (`-F 1`), opens no port (`port 0`, `cmdport 0`), and supports NTS. |
| network | a DHCP client split into a parser and an applier, [open](#open-questions) | all, except where the kernel command line configures the network | 1 | The parser handles packets from the network with no capability; the applier holds `CAP_NET_ADMIN` and can reach only routing netlink. No hook script, so no shell is needed (S1). qemu in M0 and M1 uses the kernel's `ip=` instead. |
| renew-felixd, renew-broker, renew-controlplane | `nl-renew` ([0008](https://github.com/GetFelix/ninelives/blob/docs/ca-decision/docs/decisions/0008-ca-keys-and-machine-certificates.md), proposed) | the roles of the service it renews | 3 | Renews one service's certificate and trust bundle at that service's uid, writing only that service's credential directory. The init starts it like any other long-running service; renewal itself is `nl-renew`'s design. It starts after the join, so the first certificate is already there. |
| join | the join ([#5](https://github.com/GetFelix/ninelives/issues/5)) | all | 2, first boot only | The join's uid never runs again after it succeeds (T71). PID 1 starts it only when `$DATA/join/role` does not exist; the join writes that file last, after everything else it writes is synced. A machine with a broken join state is wiped and rejoins. |
| felix-controlplane | Felix release | core | 3 | |
| felix-broker | Felix release | core, storage | 4 | Needs the control plane. |
| felixd | felixd release | all | 5 | Needs Felix ([felixd-contract.md](../felixd-contract.md#starting)). |

Phase 0 is part of early boot ([Boot, as root](#boot-as-root) step 10): its
services are `once`, run before the drop, and any result but exit 0 is
`emergency`, because a machine without its packet filter must run nothing.
`init check` allows only `once` services in phase 0. A role skips the phases
it has no service in.

**Role-gated services start after the join.** A service whose `roles` is
not every role needs the role, and the role comes from `$DATA/join/role`,
which the join writes. So `init check` rejects a role-gated service in a
phase at or below the join's. On a later boot PID 1 reads the role before
phase 1. On first boot it reads it when the join exits 0, then starts the
next phase. If the file is missing, fails the checks in the failure table or names
an unknown role, PID 1 fails closed: no role-gated service starts, the
reason is recorded, the machine stays up `degraded`, and it does not
reboot, because a reboot cannot fix a join that already ran.

A machine that is wiped and rejoins comes back as a new member with a new
node id, never as the old member resuming. That is the join's job, not the
init's, and the same rule as Kafka KRaft's directory id
([KIP-853](https://cwiki.apache.org/confluence/display/KAFKA/KIP-853%3A+KRaft+Controller+Membership+Changes)):
a disk that lost its state must not answer for state it no longer has.

### The config

One file, `/usr/lib/ninelives/init.toml`, on the read-only root. The image
build writes it, and it is covered by the root's dm-verity hash like
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
env = { FELIX_AUTH_TOKEN_FILE = "/var/lib/ninelives/credentials/felixd/token", FELIX_CA_FILE = "/var/lib/ninelives/credentials/felixd/ca.pem", FELIX_CLIENT_CERT_FILE = "/var/lib/ninelives/credentials/felixd/client.pem", FELIX_CLIENT_KEY_FILE = "/var/lib/ninelives/credentials/felixd/client.key" }
join_env = ["FELIX_BROKERS", "FELIX_AUTH_TENANT", "FELIX_CONTROLPLANE_URL", "FELIX_SERVER_NAME", "FELIXD_NODE_ID"]
ambient = []                        # capabilities; non-empty only for firewall, time, network
seccomp = "/usr/lib/ninelives/seccomp/felixd.bpf"
landlock.read = ["/usr/lib", "/etc/ssl/certs", "/run/resolv", "/proc", "/var/lib/ninelives/credentials/felixd"]
landlock.execute = ["/usr/lib/felixd/felixd", "/usr/lib/x86_64-linux-gnu"]
landlock.write = []                 # felixd writes nothing locally
landlock.devices = ["/dev/null", "/dev/urandom"]
stop_timeout_s = 135
rlimits = { nofile = 65536 }
oom_score_adj = 0                   # the default; -1000 is refused
cgroup = { cpu_weight = 100, memory_min = "0", memory_low = "0", memory_max = "2G", io_weight = 100, pids_max = 1024 }
quota = "1G"                        # project quota on state_dir
```

What each part is for:

- `uid` and `gid` are numbers. The init does not parse `/etc/passwd` or use
  NSS, and the image build checks that the numbers match the users it
  creates. No service uid or gid may be 0, `init_uid`, or another service's,
  except a renewal helper's: `renews = "felixd"` runs it at that service's
  uid and gid, and `init check` allows it only with a Landlock write list of
  exactly that service's credential directory.
- `env` names files, not secrets. `FELIX_CLIENT_CERT_FILE` and
  `FELIX_CLIENT_KEY_FILE` are proposed names: felixd 0.1.0 presents no
  client certificate, and the names are settled in
  [felixd#275](https://github.com/GetFelix/felixd/issues/275).
- `join_env` names values the join learns (brokers, tenant, control plane,
  node id) and writes to `$DATA/join/env`. They cannot be in the image. The
  init passes **only** the names listed here, and only to this service. See
  [Config integrity](#config-integrity) for why this is not config from
  writable state. `init check` requires `env` and `join_env` to be disjoint,
  rejects `LD_*` and `GLIBC_TUNABLES` in either, and rejects `join_env` on a
  service with a non-empty `ambient` list, so a value from writable state
  never reaches a process that holds a capability.
- `seccomp` is a compiled BPF program built and reviewed at image build. The
  init loads it and never compiles a filter. Whatever else a filter allows,
  it allows `prlimit64` only with pid 0, `setpriority` only with
  `PRIO_PROCESS` and who 0, `ioprio_set` only with `IOPRIO_WHO_PROCESS` and
  who 0, and `sched_setattr`, `sched_setscheduler`, `sched_setparam` and
  `sched_setaffinity` only with pid 0. The kernel allows those calls between
  any two processes with the same uid, and a service shares its uid with its
  launcher, so without these rules a service could set its launcher's
  `RLIMIT_CPU` to 0 and kill it. The filter allows only pid 0, which means
  the calling thread, so a thread can still set its own affinity or policy.
  glibc's `pthread_setaffinity_np` and `pthread_setschedparam` pass the
  thread's tid instead, and a filter cannot tell a tid of the caller's own
  from its launcher's pid, so those calls are refused; a service sets a
  thread's affinity from that thread with pid 0. Felix already does, with
  `sched_setaffinity(0, ...)` in
  [`core_shards.rs`](https://github.com/GetFelix/felix/blob/main/services/felix-broker-service/src/serving/core_shards.rs).
  The image build checks every filter for
  these rules, and checks every profile against a syscall trace recorded
  from the qemu boot, failing on a traced call the profile does not allow.
- `oom_score_adj` is the service's OOM score, 0 unless set. `init check`
  refuses -1000.
- `landlock` lists exactly what the program needs. Every path not listed,
  including `/dev`, `/sys/fs/cgroup`, `/run/ninelives` and the rest of
  `$DATA`, is denied. The profile test below runs the real program under its
  real profile, because a missing path shows only as a crash loop.
  `init check` refuses a write rule at or under `/proc`.
- `ambient` is the only way a service gets a capability, and only the
  firewall, time and network services have one
  ([What each service keeps](#what-each-service-keeps)).
- `quota` is an ext4 project quota on the state directory, so one service
  filling the disk cannot starve Felix or the init's records.
- `kind = "once"` is for the firewall (phase 0) and the join: each must
  exit 0 before the next phase starts. The join also has
  `first_boot = true`, which makes PID 1 skip it once `$DATA/join/role`
  exists.

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
   | `/dev` | devtmpfs | `nosuid,noexec` | `CONFIG_DEVTMPFS_MOUNT` is off, so PID 1 mounts it. |
   | `/sys/fs/cgroup` | cgroup2 | `nosuid,nodev,noexec,nsdelegate,memory_recursiveprot` | |
   | `/run` | tmpfs | `nosuid,nodev,noexec,mode=0755,size=16m` | The root is read-only (S3). |

   `/dev` is mounted first: the root has no device nodes, so nothing can
   reach the console until it exists.

2. Read and check the config. It is on the read-only root and no service has
   run yet, so nothing untrusted can have written it. It has to be read as
   root because the mounts, directories and cgroups below come from it.
3. The config's `[[mount]]` entries.
4. The data partition: poll `/sys/class/block/*/uevent` for a partition
   whose `PARTNAME` matches `data_partition` **on the same disk as the root
   partition** (the disk under the dm-verity device), up to
   `data_wait_s`, and mount it at `data_mount` with
   `nosuid,nodev,noexec,prjquota`. Any other disk, including one attached
   later, is never considered, and there is no retry after the drop because
   the init can no longer mount. Two matches on the root's disk mean neither
   is mounted. The init then opens `$DATA` itself (`O_DIRECTORY`) and keeps
   that fd for the life of the machine, for the shutdown `syncfs`
   ([Ordered shutdown](#ordered-shutdown)). Nothing on the data partition is executed: workload modules
   live in Felix.
5. Directories, each opened with `openat2(RESOLVE_NO_SYMLINKS |
   RESOLVE_BENEATH | RESOLVE_NO_XDEV)` and changed through the fd: `$DATA`
   (root, 0755), `$DATA/init` (init, 0700), `$DATA/join` (`nl-join`, group
   init, 2750), `$DATA/credentials` (root, 0755), `$DATA/trust` (`nl-join`,
   0755), and per service `$DATA/<state_dir>` (service, 0700) and
   `$DATA/credentials/<service>` (`nl-join`, group service, 2770). The
   setgid bit gives every file created there the directory's group, so
   PID 1 can read `join/env` and `join/role` and each service its own
   credentials; without it the join's files would get group `nl-join` and
   only the join could read them. The group write bit is for the renewal
   helper, which runs at the service's uid; Landlock keeps the service
   itself from writing there. Each
   directory gets its project id and quota, and project 0, which holds
   anything outside these directories, gets a small quota of its own, so a
   file that leaves its service's project is still bounded. Every parent
   here is root-owned, so no service can swap an entry the init opens, and
   the init changes only the directory itself, never what is in it.
   `/run/ninelives/init` (init, 0755) with the request FIFO in it (init,
   group `nl-stop`, 0620), and `/run/resolv` (the network service, 0755), are
   created the same way. The FIFO gets its group here because `fchown` to a
   group PID 1 is not in needs `CAP_CHOWN`, which it gives up.
6. The cgroup tree, every limit written, ownership set
   ([cgroups](#cgroups)), and the init moved into `ninelives/init`. Before
   the limits, the init enables the iocost controller on the root's disk
   (`io.cost.qos` at the cgroup root, `enable=1`), because `io.weight` does
   nothing without it; the kernel builds it in (`BLK_CGROUP_IOCOST`). If that
   write fails the machine boots `degraded` with the reason recorded, since
   only the I/O weights are lost.
7. Kernel settings that must not change later: host name from `hostname`,
   loopback up, `reboot(LINUX_REBOOT_CMD_CAD_OFF)` so Ctrl-Alt-Del arrives
   as `SIGINT`, and the hard `RLIMIT_CORE` to 0 and `RLIMIT_NOFILE` to the
   largest any service needs. PID 1 writes no sysctls: `fs.suid_dumpable = 0`
   and `kernel.yama.ptrace_scope = 3` (no process may ptrace another, and the
   value cannot be lowered until reboot) come from `sysctl.*` parameters on
   the kernel command line. PID 1 only bind-remounts `/proc/sys` read-only,
   so no later process can change a sysctl, whatever capabilities it holds.
   The remount needs `CAP_SYS_ADMIN`, which PID 1 still holds because the
   drop is step 11. There is no `core_pattern` to set: the kernel is built without core dumps
   ([kernel.md](https://github.com/GetFelix/ninelives/blob/docs/kernel-config/docs/components/kernel.md#core-dumps)).
8. Open, close-on-exec: the console, `/dev/kmsg` write-only (the fallback
   when the console fails; only root can open it for writing, so it cannot
   be opened after the drop), `/dev/kmsg` again read-only and non-blocking
   for the kernel log (with `dmesg_restrict` the open needs `CAP_SYSLOG`,
   which nothing holds after the drop), `/dev/watchdog` if present, and each
   `/dev/input/event*` that reports `KEY_POWER`.
9. Fork the launchers ([Launchers](#launchers)), before any thread exists.
10. Run phase 0: send `start` to the firewall's launcher and wait up to 30 s
    for the loader to exit 0. Anything else is `emergency`. The launcher
    exits after. Loading the ruleset as part of early boot means no process
    holds netfilter access once services run: the DHCP applier's
    `CAP_NET_ADMIN` reaches only routing netlink
    ([What each service keeps](#what-each-service-keeps)).
11. Start the worker thread, with every signal already blocked, then drop
    privilege ([Privilege](#privilege)).

The host name is the image's fixed one. The machine's identity is
`FELIXD_NODE_ID`; deriving the host name from the join's output would mean
reading service-written data as root.

### Launchers

A process that can start other processes under several uids can become any
uid, including 0, and with uid 0 it can rewrite root-owned files. So PID 1
never holds
`CAP_SETUID` or `CAP_SETGID` after boot. Instead, before the drop, it forks
one launcher per service, and each launcher:

1. closes every fd except its end of the socketpair and its service's pipe
   write ends (`close_range`), so it holds nothing PID 1 opened for itself
   or for another service: the console, `/dev/kmsg`, the watchdog, input
   devices, the `$DATA` fd, other launchers' sockets and other services'
   pipes;
2. opens `/dev/null`, its service's `run/` cgroup directory and its own
   `/proc/self/oom_score_adj` for writing, the only files it needs later;
3. sets its `oom_score_adj` to -1000 while still uid 0, so the OOM killer
   never picks it over its service (this must precede step 6; see
   "The OOM score" below);
4. sets securebits `NOROOT` and `NO_SETUID_FIXUP` with both locks (S24), so
   nothing it starts gains capabilities from uid 0, and the uid change below
   leaves the capability sets for step 6 to narrow;
5. limits its bounding set to the service's `ambient` list;
6. `setgroups([])`, `setresgid` and `setresuid` to the service's ids, then
   `capset` with permitted and inheritable equal to the `ambient` list and
   effective empty;
7. sets `PR_SET_DUMPABLE` to 0 and `PR_SET_NO_NEW_PRIVS`;
8. builds the service's Landlock ruleset and loads its seccomp program into
   memory, once, so no start needs to open a path;
9. applies its own Landlock ruleset and seccomp filter: the service's,
   widened only by what starting it takes. Its own loop adds `recvmsg`,
   `sendmsg`, `pipe2`, `clone3`, `read`, `write`, `close`, `brk`, `mmap`,
   `munmap` and `exit_group`. The child adds `rt_sigprocmask`,
   `rt_sigaction`, `setsid`, `dup3`, `close_range`, `chdir`, `prlimit64`
   (pid 0 only), `prctl`, `capset`, `landlock_restrict_self`, `seccomp` and
   `execve`. Filters and Landlock domains stack, so the service still ends
   with exactly its own profile, and the service's domain nests inside the
   launcher's, so the service cannot signal or ptrace it;
10. waits on its end of a `SOCK_SEQPACKET` socketpair PID 1 created.

A launcher is the same binary as the init, so nothing new ships. It stays in
`<service>/launcher` in the cgroup tree for the life of the machine and is
idle except while starting its service. It runs at its service's uid, so
without step 9 a compromised service could ptrace it and run outside its own
filter; `PR_SET_DUMPABLE`, `ptrace_scope = 3` and Landlock each stop that on
their own. The service's filter stops the rest of what a same-uid process
can do to it (`prlimit64`, priorities, scheduling; see the `seccomp` item
under [The config](#the-config)).

Each message is one datagram of at most 8 KiB with a fixed layout per type;
only `start` has a variable part, the values:

| From | Message | Meaning |
|---|---|---|
| PID 1 | `start(generation, join_env values)` | Start one instance. |
| launcher | `ready(generation)`, with the child's pidfd as `SCM_RIGHTS` | The child finished its setup and waits before `execve`. |
| PID 1 | `go(generation)` | PID 1 has recorded the pid; the child may `execve`. |
| launcher | `setup_failed(generation, step, errno)` | The child exited before `execve`, or `execve` failed. |

The launcher does not trust `start`. It was forked with the parsed config,
so it holds its service's `join_env` list itself, refuses a `start` whose
names differ from it, and checks each value again (printable ASCII, under
1 KiB). A compromised PID 1 can then pick only values the join could have
written, for names the image chose, and never for a service with a
capability (`init check`, above).

The launcher creates each instance with `clone3(CLONE_PARENT |
CLONE_INTO_CGROUP | CLONE_PIDFD)`. `CLONE_PARENT` makes the service PID 1's
child, not the launcher's, so PID 1 gets its `SIGCHLD` and its exit status
from the kernel, not from a message, and PID 1's pid-reuse argument below
still holds. Since every service is PID 1's child, being a child proves
nothing about which service a process belongs to. So PID 1 takes the pid
from the pidfd's fdinfo, checks it with `waitid(P_PIDFD, fd, WEXITED |
WNOHANG | WNOWAIT)`, and requires it in that service's `run/cgroup.procs`,
which no process can enter except through that launcher's
`CLONE_INTO_CGROUP`. The pidfd names the process the launcher created, not a
number it typed.

**The OOM score.** A child inherits its launcher's -1000, which would make
every service unkillable by the OOM killer. So around each `clone3` the
launcher writes the service's `oom_score_adj` through the fd from step 2,
and -1000 again straight after. Step 3 must come before the drop: setting
-1000 while still uid 0 also sets the launcher's `oom_score_adj_min` to
-1000, and that is what lets it write -1000 again after each `clone3`
without `CAP_SYS_RESOURCE`. The launcher writes for the child because the
child cannot reach its own file: Landlock rules attach to inodes, and the
child's `/proc` entry did not exist when the launcher's ruleset was built.
The child inherits that minimum, so the kernel would let a service set its
own score to -1000 without a capability. Only Landlock stops it: no
service's ruleset allows a write under `/proc`.

The child waits for `go` because a service that exits right after `execve`
could otherwise be reaped before its pid reached PID 1, which would count it
as an orphan, lose its exit status and leave the service in `starting`.
Driving exits from `populated 0` alone would also lose the exit status, which
the records and `crash-looping` need; the hold costs one round trip per
start. If a start produces neither `ready` nor `setup_failed`, `populated 0`
after it ends the attempt, and an attempt still unresolved after 10 s is
killed through `cgroup.kill` and retried with backoff, so a silent launcher
cannot hang a service in `starting`.

**A launcher that dies cannot be replaced**: re-forking it at the service's
uid would need `CAP_SETUID`. PID 1 sees it from `SIGCHLD` and the socket's
end of file. During an ordered shutdown that is expected and recorded as
`stopped`. At any other time PID 1 records `launcher lost`, and the machine
goes `degraded` at once with the service named as the reason. A running
instance keeps running, is reaped by PID 1, and is still stopped at
shutdown through `CAP_KILL` and `cgroup.kill`. What happens when the
service next needs a start depends on the service:

- **Felix services, felixd and their renewal helpers: ordered shutdown,
  then reboot.** A machine that can never restart its broker or felixd
  would sit in the cluster degraded forever, and one that cannot renew a
  certificate loses it within 24 h. A reboot brings fresh launchers and
  recovers without an operator; a power-off would need one. PID 1 first
  writes a `launcher-loss-reboot` record to `$DATA/init` and `fsync`s it,
  and does not reboot if that fails.
- **The reboot is rate limited across boots.** At most 2
  `launcher-loss-reboot`s in any 24 hours. Each boot's first record carries
  a boot counter, one more than the previous boot's, and every record
  carries `CLOCK_MONOTONIC` uptime, and PID 1 also writes an uptime record
  every hour. PID 1 measures the last 24 hours by
  summing uptime back through the boots in its records, taking each earlier
  boot's uptime from its last record, which never overstates it. After a panic or power cut the last record is at
  most an hour old, so the sum is at most an hour short. It errs toward
  undercounting, which can only make PID 1 refuse a reboot it could have
  made; refusing leaves the node up and `degraded`, while overcounting
  could allow a reboot loop. Wall time
  plays no part, because the clock can be set. If 2 such reboots fall in
  that window, a further loss does not reboot: the service is `failed`, the
  machine stays up and `degraded`, and the reason says so. If the records
  cannot show the count (a record is torn, a boot in the counter sequence
  has none, or they reach neither 24 hours back nor the first boot), PID 1
  does not reboot either. A launcher lost to a bug would be lost again, a
  machine rebooting in a loop flaps in and out of the cluster, and a
  process that can kill a launcher must not be able to reboot every node
  forever.
- **Every other service: `failed`, machine `degraded`.** It is never
  restarted. Losing time sync or DHCP renewal leaves the machine useful, and
  both show in the records.

Nothing kills a launcher but a bug, PID 1 at shutdown, or a process with its
uid, and its service cannot (see [Escalation](#escalation-through-the-init)).

### Privilege

PID 1's drop, after the launchers exist:

1. Set securebits `NOROOT`, `NO_SETUID_FIXUP`, `NOROOT_LOCKED` and
   `NO_SETUID_FIXUP_LOCKED` (S24). Locked, they cannot be cleared, so uid 0
   would grant no capability even to a process that regained it.
2. Limit the bounding set to `CAP_KILL` and `CAP_SYS_BOOT`.
3. `setgroups([])`, `setresgid(init_gid × 3)`, `setresuid(init_uid × 3)`.
   The filesystem uids follow. With `NO_SETUID_FIXUP` the capability sets
   are left for step 4 to narrow.
4. `capset`: permitted `CAP_KILL` and `CAP_SYS_BOOT`, inheritable and
   effective empty. Set `PR_SET_DUMPABLE` to 0 so no process can ptrace
   PID 1 or read its memory through `/proc`.
5. `PR_SET_NO_NEW_PRIVS`.
6. Load PID 1's own seccomp filter, an allow-list of what its event loop and
   worker use. The `setuid` family, `setgroups`, `mount` and the new mount
   API, `ptrace`, `bpf`, `kexec_load`, `init_module`, `quotactl` and `clone`
   are not on it; the launchers do the cloning. `capset` is on it, because
   PID 1 raises its two capabilities to effective around their calls.
   Seccomp cannot read the struct `capset` points to, so the filter cannot
   limit it; the kernel does, refusing anything outside the permitted set.

If any step fails, PID 1 reports the step and errno on the console and
powers off ([Failure modes](#failure-modes)).

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
few devices, forking the launchers and the packet filter loader's run,
which itself is at the firewall's uid with only `CAP_NET_ADMIN`. Its inputs are the kernel, the
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
| `KEY_POWER` press on an input device | the platform's power button: an ACPI button (GED) on both x86_64 and aarch64 | ordered shutdown, then power off |
| a byte on `/run/ninelives/init/request` | `r` reboot, `p` power off; anything else ignored | as above |
| any other signal | | queued in the `signalfd`, recorded and ignored |

The four signals have three meanings, following the sysvinit and busybox
convention. After the drop only the kernel can send them: no other process
has the init's uid or `CAP_KILL`. The request FIFO is owned by the init,
group `nl-stop`, mode 0620, set in boot step 5; the group has no members in production, so it
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
single-threaded. It built the Landlock ruleset and the seccomp program at
fork, and builds the rest the child needs (argv, environment, a
close-on-exec error pipe, the service's OOM score) before the clone, so the
child only makes system calls. The
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
8. Waits for one byte on a close-on-exec pipe, which the launcher writes
   when PID 1 sends `go`. End of file instead means `_exit`.
9. `execve` with an environment built from nothing: the config's `env`, then
   the `join_env` values the launcher checked.

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
    │   └── firewall/ time/ network/ join/ renew-*/    one service group each
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

Ownership does not stop a service moving its launcher into `run/`, where a
`cgroup.kill` or an OOM kill would take it: the service's uid owns both files
that move needs. Landlock does, because no service's ruleset includes
`/sys/fs/cgroup`.

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

1. Kill every service group, launchers included. A launcher that exits from
   here on is recorded `stopped`, never `launcher lost`. An orphan stays in
   the cgroup of the service that created it, because cgroup membership
   survives reparenting, so this takes every orphan too; the init only reaps
   them.
2. Write the shutdown record (see below) and `fsync` it.
3. Close every fd the init holds on `$DATA` except the one from boot step 4.
4. `syncfs` on that fd, then `sync()`, bounded at 30 s together.
5. If `syncfs` returned 0, append a `synced` record, `fsync` it, close it.
6. The final record on the console, then raise `CAP_SYS_BOOT` and call
   `reboot(2)` with `RB_AUTOBOOT` or `RB_POWER_OFF`.

Steps 2 to 5 can block in the kernel. They run on the worker thread while the
main loop keeps petting the watchdog and enforcing the bounds; if a bound
passes, the init records it on the console and calls `reboot(2)` anyway. A
machine left half shut down is worse than one that looks like it lost
power, which Felix recovers from.

Why no unmount: once no process but the init has the filesystem open and
`syncfs` has returned 0, every write is on the device. `syncfs` reports a
writeback error (Linux 5.8 and later) only if it happened after its fd was
opened, because the kernel samples the filesystem's error sequence at open,
so the fd opened right after the mount is the one that sees every error of
this boot. The ext4 journal replays committed transactions at the next
mount, which loses nothing. An unmount would add a clean superblock flag and
save that replay, but it fails with `EBUSY` while a stuck process holds a
file, and it needs `CAP_SYS_ADMIN` in PID 1.

| Service | Deadline | Why |
|---|---|---|
| felixd | 135 s | felixd's worst case is about `2 s × 64 workloads + 2 s` = 130 s ([felixd-contract.md](../felixd-contract.md#stopping)), plus 5 s for the process to exit. |
| felix-broker | 30 s, proposed | Felix documents no stop bound. Open question. |
| felix-controlplane | 30 s, proposed | Same. |
| join, network, time, renewal helpers | 5 s each | They hold no state that must be written; a renewal cut short keeps the old certificate. |

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
| `/dev/console`, prefixed `ninelives-init ` | from the first instruction | The only channel before `/run` and `$DATA` exist, and what CI reads on the serial port. The `up` record is the ready line the CI `boot` job waits for. |
| `/run/ninelives/init/state.json` | after `/run` is mounted | A snapshot of current state, replaced with write-and-rename, readable by the diagnostics service (S11). Includes the longest main loop iteration so far. |
| `$DATA/init/records.jsonl` | after `$DATA` is mounted | The history that survives a reboot (S11). Two files of 4 MiB, rotated. Records from before the mount wait in a 1024-record ring. |

Each service's stdout and stderr go to `$DATA/init/output/<service>`, two
files of 16 MiB each, rotated, written by the init. Before `$DATA` is
mounted, output is held in a 64 KiB buffer per service. The kernel log,
read from the `/dev/kmsg` fd opened in boot step 8, is kept the same way in
`$DATA/init/output/kernel`, since no process can read it after the drop.
Service output never
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

Each console line is `ninelives-init `, then one JSON object on one line,
ASCII only (any other byte is escaped). Its fields come in a fixed order:
`v` (the format version, 1), `boot` (the kernel's boot id, `null` until
`/proc` is mounted), `mono_ms`, `wall_ms`, `machine`, then whichever of
`signal`, `action`, `step`, `result`, `error`, `orphans_reaped` and
`dropped` apply. The panic hook's line carries only `v`, `mono_ms` and
`panic`. The ready line is the first that starts with
`ninelives-init {` and contains `"machine":"up"`:

```
ninelives-init {"v":1,"boot":"0f6c2a3e-6d3c-4c58-9c55-6a4b1f3e2d10","mono_ms":1234,"wall_ms":1763500000000,"machine":"up"}
```

None of this needs Felix, which is how state is reported before Felix is
up. Publishing records to Felix is M4.

### Implementation constraints

- One thread runs the event loop (`signalfd`, the pipes, `/dev/kmsg`, the
  launcher sockets, the power button, the request FIFO, each service's
  `cgroup.events`, a timeout from the nearest deadline). One worker thread
  runs the calls that can block in the kernel (`syncfs`, `sync`, `fsync`,
  and reading `$DATA/join/env`, `$DATA/join/role` and the previous boot's
  records), started after the launchers are forked and the signal mask is
  set.
- Deadlines use `CLOCK_MONOTONIC`. A clock step from time sync changes no
  decision.
- The decision logic is a pure state machine over events (signal, child
  exit, launcher message, timer, output, cgroup event) and returns actions.
  The system call layer executes them. This is what makes most invariants
  testable without a VM.
- `unsafe` lives in `mod sys` ([0005](../decisions/0005-rust-and-unsafe.md)):
  `clone3` and the child's setup, the seccomp loads, `capset`, and whatever
  rustix does not cover. Dependencies: rustix, libc for the signal mask
  and `signalfd` (rustix wraps neither), serde and toml for the config.
  Records are written by a small encoder of our own, so the panic hook can
  build one without the heap. Each dependency gets its sentence in the pull
  request that adds it.
- The binary is `no_main`. Rust's usual entry opens `/dev/null` on any of
  fds 0 to 2 that is closed and aborts if it cannot. The kernel opens the
  console on them from its built-in rootfs, so they are closed only on a
  machine with no console device; there our root has no `/dev/null` until
  PID 1 mounts `/dev`, and the abort would panic the kernel before the init
  ran. After the `/dev` attempt the init fills any closed one (the console,
  else `/dev/null`, else an `O_PATH` handle) so later descriptors never land
  on 0 to 2, and marks all three close-on-exec.

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
- **I10.** `reboot(2)` follows a `syncfs` attempt on the `$DATA` fd opened at
  mount, made after every
  service cgroup was empty or reported stuck, except where a bound passed or
  no thread could be started to run it, either of which is recorded on the
  console first.
- **I11.** A service's environment contains exactly its `env` and its
  listed `join_env` names, and it inherits no file descriptor besides 0, 1
  and 2.
- **I12.** What the init runs, as whom, with what privilege, is decided only
  by the config on the read-only root. Nothing on `$DATA`, the command line,
  a signal, a launcher message or the request FIFO changes it. A missing or
  malformed config starts no service.
- **I13.** The init never opens a file under `$DATA/credentials/` or
  `$DATA/trust/`, and no record or output it writes contains a value from
  `join_env`.
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
  its `ambient` list. Securebits `NOROOT` and `NO_SETUID_FIXUP` are set and
  locked on every process.
- **I18.** No service starts unless every step of PID 1's drop and its
  launcher's drop succeeded.
- **I19.** After boot, no sysctl changes, and no root-owned file outside
  `/proc`, `/sys` and `/dev` is writable.
- **I20.** A launcher holds no fd but its socket, its service's pipe write
  ends, `/dev/null`, its `run/` directory and its own `oom_score_adj`, and
  runs under a seccomp filter and Landlock ruleset no wider than its
  service's plus the calls that start it. Its `oom_score_adj` is -1000
  between starts; every service's is its configured value.
- **I22.** No service can change another process's resource limits,
  priority, I/O priority or scheduling, its launcher's included.
- **I23.** Lost launchers reboot the machine at most 2 times in any 24
  hours of uptime summed across boots from the records, never by wall time,
  only for Felix, felixd and their renewal helpers, and only after the
  reboot's record is on disk. A torn or missing record that the count needs
  means no reboot. A launcher that exits during an ordered shutdown is never
  counted as lost.
- **I21.** Every exit of a service's main process is attributed to that
  service, never counted as an orphan, and no start stays unresolved longer
  than 10 s.
- **I24.** No role-gated service starts before PID 1 has read a valid role
  from `$DATA/join/role`, and on first boot not before the join has exited
  0. A missing or invalid role starts no role-gated service and never
  causes a reboot.

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
| A Felix, felixd or renewal launcher dies | `SIGCHLD`, socket end of file | `degraded` at once, the service keeps running; when it next needs a start, ordered shutdown and reboot. A third loss within 24 h of summed uptime, a torn or missing record the count needs, or a failed record write: `failed`, `degraded`, no reboot | no, rebooted on purpose; yes once the limit is reached |
| Any other launcher dies | `SIGCHLD`, socket end of file | `launcher lost`, machine `degraded`; a running instance is left to run and still stopped at shutdown; `failed` and no restart until reboot | yes, without that service |
| A service tries to kill or starve its launcher with `prlimit64`, `setpriority`, `ioprio_set` or `sched_set*` | Its seccomp filter | Refused: each is allowed only on the caller itself | yes |
| A launcher sends a malformed or unexpected message, a wrong pidfd or extra fds | Bounded sizes, fixed layouts, generation numbers, one pidfd checked with `waitid` and against that service's `run/cgroup.procs`, any other fd closed | Ignored and recorded; the service's state follows the kernel's `SIGCHLD` and `cgroup.events`, not the message | yes |
| A launcher never answers a `start` | 10 s bound | `cgroup.kill` on `run/`, retried with backoff | yes |
| A permanent setup error (binary, Landlock, seccomp, ambient raise) | `setup_failed` | `failed`, machine `degraded`, others keep running | yes |
| A transient start error (`EAGAIN`, `ENOMEM`) | `setup_failed` or `clone3` error | Retried with backoff | yes |
| felixd starts before Felix answers | felixd exits 1 | Ordinary restart with backoff | yes |
| The join never succeeds | Non-zero exits or `run_timeout_s` | Retried forever with backoff; role services never start; reported | yes |
| `$DATA/join/role` missing or invalid when the role is needed (after the join exits 0, or on a later boot) | Read on the worker thread, checks as in the hostile-file row, role not in the config | No role-gated service starts; reason recorded; `degraded`; no reboot and no rerun of the join in this boot | yes, without role services |
| The packet filter loader fails or times out | Exit status, 30 s bound | `emergency`, as below: nothing runs without the S7 ruleset | yes, does nothing else |
| A fixed early mount fails | `mount` error | `emergency`: the drop still happens, no service starts, the error repeats on the console every 30 s, stop requests and reaping still work | yes, does nothing else |
| Config missing, unreadable or malformed | Read or parse error | Fail closed: `emergency`, as above. Never a default config. | yes, does nothing else |
| **Any step of PID 1's drop fails** | The call's error | No `start` is ever sent. PID 1 kills every launcher, reports the step and errno on the console, `syncfs`s `$DATA` and powers off. Staying up is not an option, because S24 forbids a running machine with a root PID 1; power off rather than reboot because a broken drop would fail again. | no, powered off on purpose |
| A launcher's drop fails | It exits before its socket is ready | As a lost launcher: a rate-limited reboot for Felix, felixd and renewal helpers, otherwise that service `failed`, machine `degraded` | depends on the service |
| Data partition missing, duplicated or unmountable | Not found within `data_wait_s`, two matches, `mount` error | `degraded`: firewall, time and network run, nothing else; never formatted; no retry, a reboot retries | yes |
| Data partition full despite quotas | Write errors | Dropped and counted on the console | yes |
| Console blocked or absent | `EAGAIN`, open failure | Dropped and counted; `/dev/kmsg`, opened in boot step 8, used if the console cannot be opened | yes |
| Disk hangs during shutdown | `syncfs` bound passes | Recorded on the console, `reboot(2)` anyway; `previous_shutdown` reads `forced` | yes |
| No thread can be started to run the sync (the worker is gone and one more spawn fails) | The spawn's error | The sync is not run on the loop's thread, where it could block without bound. A `sync` record with `"result":"not run"` and the error goes out, then `reboot(2)`; `previous_shutdown` reads `forced` | yes |
| Power cut during shutdown | Next boot reads `previous_shutdown: unknown` | Felix recovers as after any crash | yes (the next boot) |
| Platform cuts power before the budget we assumed | Same as a power cut | Same; the budget table is wrong and is fixed | yes (the next boot) |
| Clock stepped by time sync | Not needed | Deadlines are monotonic | yes |
| The init's loop hangs (a bug) | Watchdog not petted | Hardware or softdog reset after 60 s | no, reset |
| The init panics | Panic hook | Message on the console, abort, kernel panic, reboot after 10 s | no |
| An allocation in the init fails | Rust aborts | As a panic. The init's memory is bounded by its fixed buffers, and the kernel never OOM-kills PID 1. | no |
| Kernel oops | `oops=panic` | Reboot after 10 s | no |
| Hostile `$DATA/join/env` or `$DATA/join/role`: unknown names, `LD_PRELOAD`, huge file, binary bytes, a symlink, a FIFO or device | Opened on the worker thread with `O_NONBLOCK \| O_NOFOLLOW`, beneath the `$DATA` fd; `fstat` must show a regular file of at most 4 KiB; per-service allowlist, value checks | Values must be printable ASCII under 1 KiB; `LD_*` and `GLIBC_TUNABLES` are refused even if listed; anything else rejects the whole file, the service does not start, `degraded` | yes |
| Hostile symlink planted in a state directory | Not followed: the init opens only the directory itself, beneath root-owned parents, without following links | Nothing outside is touched | yes |
| A service signals or ptraces PID 1, its launcher or another service | Kernel permission checks, Landlock scoping, `ptrace_scope = 3`, non-dumpable launchers and PID 1 | Refused | yes |
| A service writes the request FIFO | File permissions | Refused: not in `nl-stop` | yes |
| A service tries to leave `run/`, join its launcher's group or change a limit | File ownership and the no-internal-process rule | Refused | yes |
| A service changes its directory's project id or clears project inheritance (`chattr -P`) to escape its quota | Its seccomp filter, and project 0's quota | `FS_IOC_SETFLAGS` and `FS_IOC_FSSETXATTR` are refused in every service's filter; a file that reached project 0 anyway is bounded by its quota | yes |
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

**Prior art.** The closest model for the service rules is ChromeOS's
[sandboxing guide](https://www.chromium.org/chromium-os/developer-library/guides/development/sandboxing/):
a uid per service, the fewest capabilities, `no_new_privs`, seccomp, and a
test that enforces them. No OS we surveyed runs its host PID 1 without
root, so this design has no direct precedent to lean on.

### What the init runs as and can reach

uid 0 only until the drop ([Privilege](#privilege)), then its own uid with
`CAP_KILL` and `CAP_SYS_BOOT` permitted and nothing effective except around
their one call. No process on the machine is root after that (I17, S24).
PID 1 opens no socket to anything but its launchers, listens on nothing and
makes no network connection.

### Every input it reads

| Input | Who can write it | Validation |
|---|---|---|
| `/usr/lib/ninelives/init.toml` | The image build only. The root is read-only (S3), and dm-verity checked by the kernel (S13), against a signed kernel image from M3. | Parsed before the drop, when no service has run; unknown keys rejected, size capped, checked at image build. Missing or malformed: fail closed. |
| Kernel command line, `argv`, environment | The bootloader; signed from M3 | Ignored as PID 1. `panic=` and `oops=` are the kernel's. |
| Signals | The kernel; nobody else after the drop | Four signals, three meanings, all of them stop or reap. |
| Child exit status | The kernel, from `waitpid` | A pid that is not a service's main process or a launcher is an orphan, reaped and counted. |
| Launcher sockets | A launcher, at its service's uid, so hostile if that service is | `SOCK_SEQPACKET`, messages of at most 8 KiB with a fixed layout per type, a truncated message or control data refused, generation-checked; at most one fd, a pidfd checked with `waitid` and against that service's `run/cgroup.procs`, any other fd closed; a 10 s bound per start. State follows the kernel's events, so a lying launcher can only fail its own service. |
| Service stdout and stderr | The service, hostile by assumption | Non-blocking pipes read into fixed buffers; opaque bytes stored, size bounded, never parsed or acted on. |
| `/dev/kmsg` | The kernel, including messages a service can trigger | Read non-blocking into fixed buffers; stored as opaque bytes, size bounded, never parsed or acted on. |
| `/sys/class/block/*/uevent` | The kernel, from partition tables anyone with the disk can write | Only `PARTNAME` on the root's disk, exact match, duplicates refused, before the drop only. |
| `cgroup.events` | The kernel | Only `populated`. |
| Power button input devices | The kernel, from the platform | Fixed-size events, only `KEY_POWER` press. |
| `/run/ninelives/init/request` | The init and `nl-stop` (empty in production) | One byte, `r` or `p`, else ignored. |
| `$DATA/join/env`, `$DATA/join/role` | The join, on first boot only | Read after the drop on the worker thread, through group `init` from the setgid directory: `O_NONBLOCK \| O_NOFOLLOW` beneath the `$DATA` fd, then `fstat`, which must show a regular file of at most 4 KiB, so a planted FIFO cannot block the open. Allowlisted, see the failure table. An unknown role starts no role service. |
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
| firewall | own | `CAP_NET_ADMIN`, ambient, during early boot only | Loading the packet filter needs it. It runs once, before the drop, and exits before any other service starts. |
| time (chronyd) | `_chrony` | `CAP_SYS_TIME`, ambient | Setting the clock has no unprivileged path. chronyd's `-U` lets it start non-root. |
| network, DHCP parser | own | none | It handles packets from the network, so it holds nothing. |
| network, DHCP applier | own | `CAP_NET_ADMIN`, ambient; `CAP_NET_RAW` and `CAP_NET_BIND_SERVICE` only until it has opened the DHCP sockets and passed them to the parser | Configuring addresses and routes needs `CAP_NET_ADMIN`, sending before there is an address needs a raw socket, and the client port is 68. |
| join | `nl-join` | none | |
| renewal helpers | the service's own | none | They connect out with the service's current certificate and write its credential directory. |
| felix-controlplane, felix-broker | own each | none | Their ports are above 1024. |
| felixd | own | none | [felixd-contract.md](../felixd-contract.md#running-it): needs no root and no capabilities. |

`CAP_NET_ADMIN` in the DHCP applier is the broadest grant on a running
machine, and with it a process could rewrite the packet filter. Its seccomp
filter allows `socket` only for `AF_NETLINK` with `NETLINK_ROUTE`, so it
cannot open `NETLINK_NETFILTER`, and it reads only the parser's fixed-format
messages, never a packet. Whether a DHCP client can be split this way is an
[open question](#open-questions). No service gets
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
  (which scopes signals and, in every Landlock domain, ptrace), PID 1 and
  the launchers are not dumpable, and `ptrace_scope = 3` forbids ptrace
  outright.
- **Limits and scheduling.** `prlimit64`, `setpriority`, `ioprio_set` and
  the `sched_set*` calls need only a matching uid, so a service could set
  its launcher's `RLIMIT_CPU` to 0 and kill it. Every service's filter
  allows them only on the caller (I22). Even so, a lost Felix or felixd
  launcher costs a reboot, not a powered-off machine, so a workload that
  found another way to do this on every node would cost each node at most
  2 reboots a day (I23), after which the node stays up and `degraded`
  instead of a powered-off cluster that needs an operator.
- **Pid reuse.** PID 1 signals only its own direct children by pid (every
  service is one, through `CLONE_PARENT`), and only before reaping them. A
  pid cannot be reused until its parent reaps it, and PID 1 is that parent,
  so a signal can never hit a recycled pid. PID 1 receives a pidfd from each
  start only to tell which service a child belongs to. Everything beyond the main process is killed through
  `cgroup.kill`, which names the cgroup, not a pid.
- **cgroups.** See the ownership table: a service can only stay in `run/`,
  and no one can change a limit.
- **The launcher.** Its only input is PID 1's socket; a compromised service
  cannot write to it (Landlock's abstract socket scoping does not apply to a
  socketpair, but the service holds no fd to it, since the child's fds are
  close-on-exec). A compromised launcher has its service's uid,
  capabilities, profile and fds, and nothing more: it closed PID 1's other
  fds at fork.
- **PID 1 itself.** No `CAP_SETUID`, `CAP_SETGID` or `CAP_SYS_ADMIN`, not
  dumpable, an allow-list seccomp filter, read-only `/proc/sys`, and a kernel
  without core dumps, so no `core_pattern` to point at a program. Even a
  compromised PID 1 has no path back to uid 0.
- **File descriptors.** Every init and launcher fd is `O_CLOEXEC`, each
  launcher closes all but its own at fork (I20), and the child marks
  everything above 2 close-on-exec (I11).
- **Environment.** Built from nothing per service (I11).
- **Files the init writes.** `$DATA/init` and `/run/ninelives/init` are the
  init's, mode 0700 and 0755, which no service can write.

### Secrets

The init never reads credentials and never holds one (I13, S8). Each
service's credential directory, `$DATA/credentials/<service>/`, is owned by
`nl-join` with the service's group, mode 2770. The join writes each file
there mode 0640 with write-and-rename, so it needs no capability, the file
takes the service's group from the setgid directory, the service reads it
through that group, and no other service can.

The join runs on first boot only (T71). After that, each service's
certificate is renewed by its renewal helper, which runs at the service's
uid, may write only that service's credential directory, and is otherwise
an ordinary phase 3 service to the init
([0008](https://github.com/GetFelix/ninelives/blob/docs/ca-decision/docs/decisions/0008-ca-keys-and-machine-certificates.md),
proposed). The group write bit on the directory is for the helper; the
service's own Landlock ruleset lists the directory as read-only.

**Trust bundles.** Each service's trust bundle lives in its own credential
directory (`ca.pem`), not in a shared directory, because only the join
could write a shared one and the join never runs again, so a CA rotation
would need a wipe. The join writes the first bundle; after that the renewal
helper writes it from the issuer's renewal response. The helper accepts a
bundle only if it is signed by, or chains to, the pinned issuer root, and
every bundle carries a generation number. The helper refuses a bundle whose
generation is lower than the current one. A bundle with the same generation
must be byte-identical to the current one. Any change, adding or removing
roots, needs a strictly higher generation. Otherwise a replayed old signed
bundle could bring back a root a later generation removed. Felix reads bundles at startup
([felix#2816](https://github.com/GetFelix/felix/issues/2816)), so a new one
takes effect when the service next restarts.

`$DATA/trust/` stays, for the join only: it holds the pinned issuer root
from the join config, written once by `nl-join` and readable by every
helper. No service or helper can write it, so a compromised service cannot
change which issuer its helper believes.

Felix's broker listeners and Raft peer port require mTLS, so every Felix
client (felixd, and the Felix services toward each other) presents a
certificate bound to its token's subject. The control-plane API is the
exception until Felix can take a client CA there
([felix#2811](https://github.com/GetFelix/felix/issues/2811)): it runs
server TLS with bearer tokens. The join obtains each certificate and key
with the token and writes them beside it: `token`, `client.pem` and
`client.key`, plus a broker's or control plane's own server certificate and
key. They get the token's permissions because a stolen key is as good as a
stolen token. felixd gets only paths from the image config,
`FELIX_AUTH_TOKEN_FILE`, `FELIX_CA_FILE`, `FELIX_CLIENT_CERT_FILE` and
`FELIX_CLIENT_KEY_FILE`, and reads the files itself
([felixd-contract.md](../felixd-contract.md#running-it)). The last two are
proposed names until felixd can present a certificate
([felixd#275](https://github.com/GetFelix/felixd/issues/275)).
So no token or key passes through the init, a launcher message, a command
line or an environment. Service output may contain whatever a service prints; keeping
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
(the `boot` job runs the first, the ready line); tests that inspect
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
| Every process's `/proc/<pid>/status` (Uid, Gid with all four ids, Groups, CapPrm, CapEff, CapInh, CapAmb, NoNewPrivs, and `Seccomp: 2`, which shows only that a filter is loaded) and `/proc/<pid>/cgroup` match the config and the capability tables, PID 1 and launchers included | qemu | I3, I17, S6, S24 |
| `/proc/<pid>/status` has no securebits field, so a test-build fault hook makes PID 1 and every launcher call `prctl(PR_GET_SECUREBITS)` and write the result to the console, and each test service does the same: all show `NOROOT`, `NO_SETUID_FIXUP` and both locks; `kernel.yama.ptrace_scope` reads 3 | qemu (test image) | I17, S24 |
| The inspector dumps `/proc/<launcher>/fd` for every launcher: only its socket, its service's pipe write ends, `/dev/null`, its `run/` directory and its `oom_score_adj` | qemu | I20 |
| A test-build fault hook makes each launcher, between starts, call what its filter denies (`ptrace`, `mount`, `setuid`, `socket` outside its service's families, `prlimit64` and `sched_setaffinity` on another pid): each fails; each test service does the same against its own filter | qemu (test image) | I20, I3 |
| The inspector reads `/proc/<pid>/oom_score_adj`: -1000 for every launcher, the configured value for every service; a test service writing -1000 to its own is refused | qemu | I20, OOM score |
| A test service calls `prlimit64` with its launcher's pid and `RLIMIT_CPU` 0, `setpriority` with `PRIO_USER` and `PRIO_PGRP`, `ioprio_set` with `IOPRIO_WHO_USER`, and each `sched_set*` call on its launcher: all refused, the launcher is still alive and starts the next instance; `sched_setaffinity` with pid 0 from a second thread succeeds, and `pthread_setaffinity_np` is refused | qemu | I22 |
| A service that exits right after `execve`, started 1000 times: every exit is attributed to it, none to orphans, and it never stays `starting` | qemu | I21 |
| A test-image launcher sends a wrong generation, another service's pidfd, a pidfd of an unrelated process, extra fds, a truncated message, or nothing at all: each is ignored or times out, and state follows the kernel | qemu (test image) | I12 (launcher messages), I21 |
| A fault hook makes PID 1 send `start` with an unlisted name, `LD_PRELOAD` or a binary value: the launcher refuses it | qemu (test image) | launcher re-check |
| `init check` rejects `join_env` on a service with a non-empty `ambient` list, and a non-`once` service in phase 0 | unit | I12 |
| The packet filter loader made to fail: `emergency`, nothing else starts; on a normal boot the DHCP applier's attempt to open a `NETLINK_NETFILTER` socket is refused | qemu, fault | phase 0, S7 |
| A test service clears project inheritance on its state directory and tries to change its project id: both ioctls refused; a test-image service without those filter rules moves a file into project 0 and hits project 0's quota | qemu (test image) | quota escape |
| First-boot traces: the renewal helpers and Felix services start only after the join exits 0 and the role is read; with the role file missing, oversized, a FIFO or naming an unknown role, none starts, the machine is `degraded` and no reboot is issued; `init check` rejects a role-gated service in phase 1 or 2 | unit | I24 |
| A first boot in qemu: `renew-felixd` is running after the join, and on the second boot it starts without the join | qemu | I24 |
| After the join: `$DATA/join/env` has group init, and each credential file has its service's group and mode 0640; a second boot does not start the join | qemu | setgid directories, first boot only |
| felixd connects with its client certificate. Gated on [felixd#275](https://github.com/GetFelix/felixd/issues/275); until then the test checks the files are in place | qemu | mTLS handoff |
| A test program under the felixd renewal helper's profile replaces a file in felixd's credential directory; under felixd's profile the same write is refused, and neither can write `$DATA/trust`. The bundle acceptance rules (signed by the pinned issuer root; a lower generation is refused, the same generation only if byte-identical, any change needs a higher one; an older signed bundle replayed after a root was removed is refused) are tested with `nl-renew` | qemu (test image) | renewal, Landlock |
| Unit traces: a launcher lost while its service runs gives `degraded` and no other action; the next start gives a record, its `fsync`, ordered shutdown and `reboot`; with record histories holding 2 such reboots in the last 24 h of summed uptime, spread over several boots, `failed` and no reboot; with 2 reboots just over 24 h of summed uptime back, a reboot; a wall clock set forward or back changes nothing; a torn record, a gap in the boot counter, or records reaching neither 24 h back nor the first boot give no reboot; a failed record write gives no reboot; launchers exiting after a stop request give `stopped` | unit | I23 |
| A fault hook makes the broker's launcher exit: the broker keeps running and the machine reports `degraded`; then the broker exits: ordered shutdown and reboot. The same again reboots once more; a third time within the day: no reboot, `failed`, the reason reported. For the time service: `failed`, `degraded`, no reboot | qemu (test image) | I23, launcher rows |
| A normal power-off: no `launcher lost` record | qemu | I23 |
| A fault hook makes PID 1 call `setresuid(0, 0, 0)`, `setresuid` to a service uid, and `mount` after the drop; each fails and the init keeps running | qemu (test image) | I17 |
| Fault injected at each child setup step: the service never execs, the step and errno are reported | unit (sys mocked), qemu | I3, permanent and transient errors |
| Each real service runs under its real Landlock and seccomp profile against a single-node cluster in the image and stays `running` for 5 minutes; felixd appears in `nodes` | qemu | profiles are complete |
| A service exits leaving a daemon; the daemon is killed and the next instance starts after `populated 0` | qemu | I4 |
| A test service tries to move itself into `<service>/`, `launcher/` and another service's group | qemu | cgroup ownership |
| Backoff schedule, jitter bounds and the 60 s reset | unit | I5 |
| Phase order with a `once` service that fails twice | unit, qemu | I6 |
| A service crashes during shutdown and is not restarted | unit, qemu | I7 |
| Shutdown order and `SIGTERM` times against the config, with and without a budget; a felixd that ignores `SIGTERM` does not delay the broker's `SIGTERM` past `latest()` | unit, qemu | I8 |
| A service that ignores `SIGTERM` is killed no earlier than its deadline, and power-off lands within 265 s, or within B | qemu, fault | I9, #48 |
| Power-off with the data disk throttled to zero: `reboot(2)` after the bounds, next boot `forced` | fault | I10 |
| Event traces with services exiting, being killed and going `stuck` in every order: the `syncfs` action comes only after every service cgroup is empty or `stuck`, and `reboot` only after `syncfs` or a passed bound | unit | I10 ordering |
| A writeback error injected on the data disk (dm-flakey) before shutdown: `syncfs` reports it, no `synced` record, next boot `forced` | fault | I10, errseq |
| A test service dumps its environment and `/proc/self/fd` | qemu | I11 |
| `$DATA/join/env` planted with `LD_PRELOAD`, `GLIBC_TUNABLES`, a symlink, a FIFO, an oversized file and binary bytes; with the FIFO the main loop keeps running (watchdog petted, stop requests handled) | qemu | I12, I14, hostile inputs |
| `init check` rejects overlapping `env` and `join_env`, `LD_*`, and uid 0 or a shared uid | unit | I12 |
| A symlink planted inside a state directory pointing at `/run/ninelives`; nothing outside changes | qemu | hostile inputs |
| Known test credentials planted; console, records, output and `/run` searched | qemu | I13, S8 |
| A service writes 1 GB to stdout while the console is throttled; `state.json` reports the longest iteration under 100 ms, and sinks returning `EAGAIN` never block the fake loop | qemu, unit | I14 |
| A service fills its quota and keeps writing; the init's records and Felix's directory still accept writes | fault | I15, disk full |
| A service writes 1 GB of output and a fault hook makes the init emit records in a loop: `$DATA/init` never holds more than 8 MiB of records plus 32 MiB of output per service | qemu (test image) | I15 size bound |
| Every transition in the unit traces yields a record or a drop count per sink; console order matches | unit, qemu | I16 |
| Image build scan for setuid, setgid and `security.capability` files, seeded with one to see it fail | CI | S24 |
| Each drop step, PID 1's and a launcher's, made to fail (fault hook): no service starts under that failure; PID 1's failure is reported and powers off, a launcher's leaves its service `failed`, or reboots, within the I23 limit, for Felix, felixd and renewal helpers | fault (test image) | I18 |
| `/proc/self/mountinfo` shows `/proc/sys` read-only; a test-image probe with `CAP_SYS_ADMIN` and `CAP_NET_ADMIN` writes every sysctl and gets `EROFS`; the inspector walks every writable mount for root-owned writable files | qemu (test image) | I19 |
| Services send every signal to PID 1, their launcher and each other, try `ptrace` on each, and write the request FIFO and cgroup files: all refused | qemu | escalation |
| A test-image service with `CAP_KILL` sends every signal but the four to PID 1, `SIGABRT` and `SIGSEGV` included; nothing changes | qemu (test image) | signal table |
| A fault hook panics the init: the message is on the console and the VM reboots within 15 s | fault (test image) | panic row |
| The init loop made to hang (fault hook): the watchdog resets the VM | fault (test image) | watchdog row |
| Kernel command line with extra words and `KEY=VALUE` pairs boots identically | qemu | hostile inputs |
| A second disk, and a disk hot-attached after boot, with a partition named `ninelives-data`: never mounted | qemu | hostile inputs |
| Malformed and missing config: the build fails (`init check`); booted anyway, `emergency`, power-off still works | CI, qemu | fail closed |
| qemu `system_powerdown` on x86_64 and aarch64 produces an ordered shutdown | qemu | power button |
| Acknowledged records written under `Periodic` fsync up to the power-off request are all read back after the next boot; `felixctl data inspect segments` on the disk reports nothing to repair; `previous_shutdown: clean` | qemu | 0002's durability check |
| A test writer that, on `SIGTERM`, writes a known block and exits without `fsync`: the block is there after the next boot; with a fault hook that skips `syncfs` and `sync` the test must fail, which shows it can see a skipped sync even though Felix may fsync on close | qemu, fault (test image) | I10, durability check is sensitive |
| Power cut during shutdown, then boot: `previous_shutdown: unknown`, Felix starts | fault | power loss row |

## Open questions

| Question | Proposal | Decides | Needed by |
|---|---|---|---|
| **Felix's stop contract.** What Felix's broker and control plane guarantee on `SIGTERM` (everything acknowledged written and logs closed before exit 0, in every fsync mode, including the control plane's Raft store), and their worst-case stop time. Without it, a clean exit is evidence and the end-to-end test is the proof. | A `docs/felix-contract.md` page, like felixd's, backed by Felix docs or an issue filed in Felix; deadlines set from it | NineLives maintainers with Felix | M1 (#48) |
| Whether `felixctl` ships in the Felix release tarball, and whether anything inspects the control plane's store the same way | Check 0.7.2's artifact; file a Felix issue for what is missing | NineLives maintainers | M1 |
| A readiness signal from Felix, so felixd's early exits are not counted as restarts | Live without it; revisit if boot time suffers | NineLives maintainers | M2 |
| **Platform stop budgets.** How long each cloud waits after the power button before cutting power | Measure each target and fill the budget table; a target whose budget cannot cover Felix's deadlines is reported, not shipped silently | NineLives maintainers | Each cloud target's first M2 run |
| **Which DHCP client, and whether it splits.** It must run non-root with no hook script, as a parser with no capability and an applier whose only capability after start is `CAP_NET_ADMIN` and whose seccomp filter allows only `AF_NETLINK` with `NETLINK_ROUTE`. dhcpcd's privilege separation expects to start as root, and whether its pieces map onto this split is unverified. | Try dhcpcd in qemu; if it cannot be split this way, a small DHCP client in Rust as two processes (not PID 1) | Network design | M1 |
| DNS: something must write `/run/resolv/resolv.conf` without a shell hook | The DHCP client writes it directly, or the join records brokers by address | Network design, #5 | M1 |
| Which program loads the packet filter (S7) | A static nftables ruleset from the image, loaded by a minimal loader in phase 0, before the drop | Network design | M1 |
| How the seccomp and Landlock profiles are produced and reviewed | Recorded from qemu runs, minimised by hand, compiled at image build, reviewed on each component bump; the image build fails when a call in the recorded trace is outside its profile | Image design | M1 |
| **Authenticated time.** Clouds' metadata NTP servers do not offer NTS. Without it an on-path attacker can step the clock: certificates then fail (forward) or expired ones pass (back). chronyd is limited to stepping during its first three updates (`makestep 1 3`) and refuses offsets over 1000 s after that (`maxchange`). Whether Felix and felixd leases depend on wall time is not documented. | NTS where the servers support it; otherwise the limits above, and the threat model decides | Threat model (#56) | M1 |
| Whether Felix waits for time sync, since certificate checks need a sane clock | No wait; qemu and clouds have an RTC | Threat model (#56) | M1 |
| **Data partition integrity.** Anyone who can rewrite the disk offline reaches the kernel's ext4 parser while the init is still root, and can plant or alter Felix's data and credentials. Integrity or encryption sealed to a TPM or vTPM would close that. | Treat it as a likely requirement; #56 decides, with M3's verified boot | Threat model (#56) | M3 at the latest |
| **Threat model assumptions** this page makes: the image is trusted once built and, from M3, verified; the data partition is not trusted for what runs but is trusted against offline tampering until the question above is settled; physical access is out of scope until M3; a compromised service is the main attacker. | Confirm or correct in #56 | Threat model (#56) | M1 |
| Whether `emergency` should reboot so M3's boot counting can fall back to the other image (a failed drop already powers off) | Stay up and report until M3; M3 decides | A/B design (#8) | M3 |
| cgroup numbers (`memory.min`, `cpu.weight`) and quotas | From the N4 measurement | NineLives maintainers | M2 |
| Data filesystem features: ext4 with `quota` and `project`, reserved blocks owned by the init's uid | Adopt in #4 | Data partition design (#4) | M1 |
