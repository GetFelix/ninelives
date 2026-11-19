# init (PID 1)

The scope is fixed by [0002](../decisions/0002-our-own-init.md). This page
says how the init meets it. Nothing here is built yet; the first slice is
[#47](https://github.com/GetFelix/ninelives/issues/47) (M0) and the services
and shutdown are [#48](https://github.com/GetFelix/ninelives/issues/48) (M1).

Paths below use `$DATA` for the data partition's mount point, which the
image config names.

## What it does

The init is the first process the kernel starts and the only one that runs
as root for the life of the machine. It mounts the kernel filesystems and
the data partition, reaps every orphan, and starts the services the
machine's role needs in a fixed order of phases, each service in its own
user and cgroup v2 group with its output captured. It restarts a service
that exits, with capped exponential backoff. On a reboot or power-off
request it stops services in reverse order, giving each the deadline its
contract needs (felixd's is about 130 s), kills what is left, syncs,
unmounts and calls `reboot(2)`. Throughout, it writes its own state and each
service's state as records to the console, to `/run` and to the data
partition. It never talks to Felix.

## What it does not do

Each of these is left out on purpose. Adding one needs a change to 0002.

- **No dependency graph, socket activation or on-demand start.** Services
  start in numbered phases. That is enough for the four or five services a
  role has, and a graph solver in PID 1 is code that can be wrong at boot.
- **No readiness protocol.** A phase starts when the earlier phase's
  long-running services have been started, not when they are ready. Felix
  has no readiness signal the init could use (see
  [Open questions](#open-questions)), and felixd already exits and is
  restarted when Felix is not up yet ([felixd-contract.md](../felixd-contract.md#starting)).
- **No health checks or liveness probes.** A service is restarted when it
  exits, nothing else. Deciding that a running process is unhealthy is
  policy, and policy belongs on the log (M4).
- **No reload.** The config is read once at boot. A new config is a new image.
- **No cluster decisions.** The init never drains, reboots on its own because
  a service is failing, or changes the machine's role.
- **No Felix client.** PID 1 must not depend on what it starts. Shipping
  records to Felix is a later service's job.
- **No device management.** No udev, no hotplug handling, no module loading
  (there are no modules, [0007](../decisions/0007-kernel-from-upstream-source.md)).
  devtmpfs creates the device nodes.
- **No filesystem creation, checking or resizing.** The init mounts a
  filesystem that exists. Creating and growing the data partition is
  [#4](https://github.com/GetFelix/ninelives/issues/4)'s design and runs as a
  separate program. The init never formats anything, because formatting the
  wrong disk destroys Felix's data.
- **No network configuration beyond loopback.** DHCP and time sync are
  services. The packet filter (S7) is not the init's either.
- **No login, getty, shell or user sessions.** A development image that adds
  a serial shell lists it as a service.
- **No listening sockets and no control socket.** Requests reach the init as
  signals only (see [Security](#security)).
- **No timers, cron, suspend, hibernate or kexec.**
- **No per-service mount or network namespaces.** Separate users, a
  read-only root, Landlock and seccomp give the file and system call
  isolation S6 asks for without mount propagation rules to get right. This
  is reopened if the threat model ([#56](https://github.com/GetFelix/ninelives/issues/56))
  finds a case they miss.
- **No boot counting or A/B fallback.** That is M3
  ([#8](https://github.com/GetFelix/ninelives/issues/8)).
- **No kernel crash dumps.** S11's crash dumps need their own design.

## Interfaces

### Programs it starts

[0002](../decisions/0002-our-own-init.md) leaves the helper programs to this
design.

| Service | Program | Roles | Phase | Why this program |
|---|---|---|---|---|
| time | `chronyd` (Debian `chrony`) | all | 1 | Runs in the foreground (`-d`), drops to its own user (`-u _chrony`) keeping only `CAP_SYS_TIME`, ships its own seccomp filter (`-F 1`), and supports NTS if the threat model asks for authenticated time. Configured as a client only, no NTP port. |
| network | `dhcpcd` (Debian `dhcpcd-base`) | all, except where the kernel command line configures the network | 1 | Configures addresses and routes itself over netlink, has privilege separation, and can run with no hook script, so no shell is needed (S1). qemu in M0 and M1 uses the kernel's `ip=` instead. |
| join | the join ([#5](https://github.com/GetFelix/ninelives/issues/5)) | all | 2, run once | Runs every boot and exits 0 at once if the machine has already joined, so the init needs no "first boot" logic. |
| felix-controlplane | Felix release | core | 3 | |
| felix-broker | Felix release | core, storage | 4 | Needs the control plane. |
| felixd | felixd release | all | 5 | Needs Felix ([felixd-contract.md](../felixd-contract.md#starting)). |

The control plane and broker phases follow 0002. A role skips the phases it
has no service in.

### The config

One file, `/usr/lib/ninelives/init.toml`, on the read-only root. The image
build writes it, and in M3 it is covered by the root's dm-verity hash like
every other file (S13). TOML because the repo already uses it for
`components.toml` and its parser is a small, widely used pure-Rust crate.
Unknown keys are errors, so a typo cannot silently drop a limit.

```toml
version = 1

[machine]
data_partition = "ninelives-data"   # GPT partition name, read from sysfs
data_fstype = "ext4"                # decided in #4
data_mount = "/var/lib/ninelives"
data_wait_s = 60
default_hostname = "ninelives"
watchdog_s = 60

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
env = { FELIX_AUTH_TOKEN_FILE = "/var/lib/ninelives/credentials/felixd/token" }
join_env = ["FELIX_BROKERS", "FELIX_AUTH_TENANT", "FELIX_CONTROLPLANE_URL", "FELIX_SERVER_NAME", "FELIXD_NODE_ID"]
capabilities = []                   # bounding set kept; empty for all but time and network
start_as_root = false
seccomp = "/usr/lib/ninelives/seccomp/felixd.bpf"
landlock = { read = ["/usr", "/etc/ssl"], write = ["/var/lib/ninelives/felixd"] }
stop_timeout_s = 135
rlimits = { nofile = 65536 }
cgroup = { cpu_weight = 100, memory_max = "2G", pids_max = 1024 }
```

What each part is for, so none of it is speculative:

- `uid` and `gid` are numbers. The init does not parse `/etc/passwd` or use
  NSS, and the image build checks that the numbers match the users it
  creates.
- `join_env` names values the join learns (brokers, tenant, control plane,
  node id) and writes to `$DATA/join/env`. They cannot be in the image. The
  init passes **only** the names listed here, and only to this service. See
  [Config integrity](#config-integrity) for why this is not config from
  writable state.
- `seccomp` is a compiled BPF program built and reviewed at image build. The
  init loads it and never compiles a filter, which keeps a policy compiler
  out of PID 1.
- `start_as_root` exists for the two programs that must start privileged and
  drop privilege themselves (chronyd, dhcpcd). Each use has a comment in the
  config saying why, and its `capabilities` bounding set is the most that
  program can ever hold.
- `kind = "once"` is for the join: it must exit 0 before the next phase
  starts.

CI runs `init check` (the same binary, not as PID 1) against every image's
config, so a malformed config fails the build rather than a boot.

### Kernel command line and arguments

The kernel names the init with `init=`. The init ignores its own `argv` and
environment when it runs as PID 1: the kernel passes unrecognised command
line words to init as arguments and environment, and nothing on the command
line is allowed to change what the init does. The image's command line sets
`panic=10` and `oops=panic`, so a kernel panic, including one caused by PID 1
dying, reboots the machine after 10 s with the panic on the console. In M3
the command line is part of the signed kernel image.

### Mounts

Fixed, in this order, before the config is read:

| Target | Type | Options | Why |
|---|---|---|---|
| `/proc` | proc | `nosuid,nodev,noexec,hidepid=invisible` | Services cannot see each other's processes. |
| `/sys` | sysfs | `nosuid,nodev,noexec` | |
| `/dev` | devtmpfs | `nosuid,noexec` | The kernel may have mounted it already (`CONFIG_DEVTMPFS_MOUNT`); `EBUSY` with devtmpfs there is success. |
| `/sys/fs/cgroup` | cgroup2 | `nosuid,nodev,noexec,nsdelegate,memory_recursiveprot` | |
| `/run` | tmpfs | `nosuid,nodev,noexec,mode=0755,size=16m` | The root is read-only (S3), and the state file lives here. |

Then the config's `[[mount]]` entries, then the data partition: the init
polls `/sys/class/block/*/uevent` for a partition whose `PARTNAME` matches
`data_partition`, up to `data_wait_s`, and mounts it at `data_mount` with
`nosuid,nodev,noexec`. Nothing on the data partition is ever executed:
workload modules live in Felix. If two partitions have the name, the init
mounts neither, because guessing could mount a stale disk under Felix.

Then the host name (the node id from the join if present, else
`default_hostname`) and loopback up.

### Signals

The init blocks every signal, reads them from a `signalfd`, and handles
them in its single-threaded event loop. Nothing runs in a signal handler.

| Signal | Meaning | Action |
|---|---|---|
| `SIGCHLD` | a child exited | `waitpid(-1, WNOHANG)` until nothing is left. A pid that is a service's main process updates that service; any other pid is an orphan, reaped and counted. |
| `SIGINT` | Ctrl-Alt-Del (the init calls `reboot(LINUX_REBOOT_CMD_CAD_OFF)` at start, so the kernel sends this instead of rebooting at once) | ordered shutdown, then reboot |
| `SIGTERM` | reboot request | ordered shutdown, then reboot |
| `SIGUSR2` | power-off request | ordered shutdown, then power off |
| anything else | | recorded and ignored |

These follow the busybox and sysvinit convention so the local command is
unsurprising. A request during shutdown changes only the final action, and
only from reboot to power-off, the more conservative of the two. There is
no "force now" request: every step is already bounded.

### Local command

The same binary, run as `init reboot` or `init poweroff` with a pid other
than 1, sends the matching signal to PID 1 and exits. It needs root. A
production image has no root process other than the init and the two
helpers before they drop privilege, so in production stop requests come
from the hypervisor (Ctrl-Alt-Del). The power button is an
[open question](#open-questions).

### Starting a service

The init creates the child with `clone3(CLONE_INTO_CGROUP)`, so the process
is in its own cgroup from its first instruction. Between clone and `execve`
the child does only system calls, in this order, because each step depends
on the one before it:

1. Clear the signal mask and reset every disposition. The init blocks all
   signals and a blocked mask survives `execve`, so skipping this would make
   the service deaf to `SIGTERM`.
2. `setsid`.
3. stdin from `/dev/null`, stdout and stderr to the service's pipes, then
   `close_range(3, ~0)`. Every fd the init opens is also `O_CLOEXEC`.
4. `chdir` to the state directory, set the rlimits.
5. Drop the capability bounding set to `capabilities`. This needs
   `CAP_SETPCAP`, so it comes before the uid change.
6. `setgroups([])`, `setresgid`, `setresuid`. For a non-root target this
   clears every capability. The ambient set is never raised.
7. `PR_SET_NO_NEW_PRIVS`, so no setuid binary or file capability can give
   back what was dropped.
8. `landlock_restrict_self` with the ruleset the parent built.
9. Load the seccomp filter, last, so it can forbid everything steps 1 to 8
   used.
10. `execve` with an environment built from nothing: the config's `env`, then
    the `join_env` values. The init's own environment is never passed on.

Any failing step makes the child `_exit(127)` before `execve`, after writing
a step number to a close-on-exec pipe the parent reads. The parent reports
the step, and the service never runs with half its restrictions.

The init sends `SIGTERM` to the main process to stop it, and at the deadline
writes `1` to the cgroup's `cgroup.kill`, which kills every process in it,
including any the service forked.

### cgroups

```
/sys/fs/cgroup/                    the init, and controllers cpu memory io pids enabled
└── ninelives/
    ├── time/  network/  join/
    ├── felix-controlplane/  felix-broker/
    └── felixd/
```

For each service the init writes the config's `cpu.weight`, `memory.min`,
`memory.low`, `memory.max`, `io.weight` and `pids.max` before the first
start, and `memory.oom.group = 1` so an OOM kill takes the whole service
and the init sees it exit. Felix gets the reservation (`memory.min`, a high
`cpu.weight`); that is what N4 measures, and the numbers come from that
measurement (N3), not from this page. The sum of every `memory.max` is
checked at image build to leave room for the kernel and the init.

### Restart with backoff

- The n-th consecutive restart waits `min(1 s × 2^(n-1), 30 s)`, with ±25%
  jitter so a cluster-wide cold start does not have every felixd retry the
  same brokers in step. 1 s keeps the felixd-before-Felix race short; 30 s
  keeps a dead dependency from costing more than a lease TTL's worth of
  wasted starts per few minutes.
- The count resets once the service has run for 60 s, twice the cap, so a
  service that dies at the cap never looks healthy.
- After 5 consecutive restarts the service is reported `crash-looping`. It
  keeps being restarted.
- **Long-running services never give up.** A service usually fails because
  something else is down (Felix's quorum, the network), and it should come
  back when that does without an operator. A machine that stopped trying is
  useless and no easier to diagnose.
- **What gives up:** a start that fails before `execve` for a reason a retry
  cannot change (binary missing, seccomp file unreadable, a uid the kernel
  rejects, cgroup creation refused). The service is marked `failed`, the
  machine `degraded`, and the rest keep running. The image is broken; the
  fix is a new image.
- A `once` service that exits non-zero is retried with the same backoff,
  and the next phase waits. Its run is bounded by `run_timeout_s` (the join:
  300 s), after which it is killed and retried.
- A long-running service exiting 0 is treated as a failure, because nothing
  long-running should exit on its own.
- A new instance starts only after the old one's cgroup reports
  `populated 0` in `cgroup.events`. Two brokers on one data directory must
  never overlap.

### Ordered shutdown

On a request, the init stops starting and restarting anything, then:

1. Stops phases in reverse order. Within a phase, services stop in parallel.
   felixd goes first because it deletes its leases and node entry in Felix,
   so Felix and the network must still be up; the network goes last for the
   same reason.
2. For each service: `SIGTERM`, wait for exit up to `stop_timeout_s`, then
   `cgroup.kill`, then wait up to 5 s for the cgroup to empty. A process in
   uninterruptible sleep on a hung disk cannot die; it is reported and
   shutdown goes on.
3. Kills anything left anywhere (orphans in the root cgroup) and reaps.
4. Writes the shutdown record to `$DATA` (see below).
5. `sync`, bounded at 30 s.
6. Unmounts `$DATA`, bounded at 10 s. If that fails, remounts it read-only;
   if that fails too, reports it.
7. Unmounts the rest and calls `reboot(2)` with `RB_AUTOBOOT` or
   `RB_POWER_OFF`.

Steps 5 to 7 can block in the kernel. They run on one worker thread while
the main loop keeps petting the watchdog and enforcing the bounds; if a
bound passes, the init records it on the console and calls `reboot(2)`
anyway. A machine left half shut down is worse than one that looks like it
lost power, which Felix recovers from.

| Service | Deadline | Why |
|---|---|---|
| felixd | 135 s | felixd's worst case is about `2 s × 64 workloads + 2 s` = 130 s ([felixd-contract.md](../felixd-contract.md#stopping)), plus 5 s for the process to exit. |
| felix-broker | 30 s, proposed | Felix documents no stop bound. Open question. |
| felix-controlplane | 30 s, proposed | Same. |
| join, network, time | 5 s each | They hold no state that must be written. |

The worst case from request to `reboot(2)` is the sum of the phases'
largest deadlines plus the fixed bounds: 135 + 30 + 30 + 5 + 5 + 5 per
phase for `cgroup.kill` + 30 + 10, about 270 s. In practice felixd holds
far fewer than 64 workloads.

### How shutdown shows Felix flushed

What the init can establish, in order:

1. Each Felix service exited on its own with status 0 after `SIGTERM`,
   before its deadline, rather than being killed.
2. No Felix process remains: the service's cgroup is empty.
3. `sync` returned, and `$DATA` unmounted cleanly, which the kernel allows
   only after writing every dirty page of that filesystem.

Once a Felix process is gone, everything it wrote is in the kernel; Felix's
own [durability docs](https://github.com/GetFelix/felix/blob/v0.7.2/docs/durable-storage.md#durability-policies)
note that even `FsyncMode::None` data survives a process crash because the
page cache belongs to the kernel. Steps 2 and 3 move that to the device. So
the chain holds if and only if Felix, on `SIGTERM`, writes everything it
has acknowledged and closes its logs before exiting 0. Felix does not
document that today, so step 1 is evidence, not proof. Closing that gap
needs a Felix contract page like [felixd-contract.md](../felixd-contract.md);
see [Open questions](#open-questions).

The independent check is the one 0002 asks for: CI powers the VM off, then
inspects the data disk from outside the VM with Felix's own recovery plan
(`felixctl data inspect segments`, which reports what startup would repair
without writing), and fails if it reports any repair. Running it outside the
VM keeps the tool off the image (S1).

The shutdown record lists, per service, `exited(status)` or
`killed-at-deadline`, and the time taken. It is written before `sync` and
the unmount, so a clean unmount makes it durable. On the next boot the init
reads the previous boot's last record and reports `previous_shutdown` as
`clean`, `forced` (a service was killed or a bound passed) or `unknown`
(power was lost). A torn last line reads as `unknown`.

### Watchdog and panics

- If `/dev/watchdog` exists, the init opens it, sets `watchdog_s` (60 s),
  and pets it every 10 s from the main loop, through shutdown, up to
  `reboot(2)`. If the loop hangs, the machine resets. The kernel config
  builds in `softdog` as a fallback where there is no hardware watchdog
  (qemu has i6300esb). The watchdog proves the init's loop runs, nothing
  about services.
- The init is built with `panic = "abort"`. A panic hook writes the message
  to the console, then the process aborts, the kernel panics ("Attempted to
  kill init") and reboots after 10 s. The code rules
  ([0005](../decisions/0005-rust-and-unsafe.md)) are what keep panics out.

### State reports

Records are JSON lines, one per state change, each with a format version,
`CLOCK_MONOTONIC` and wall time, and a boot id. Machine states: `booting`,
`up`, `degraded`, `emergency`, `stopping`. Service states: `starting`,
`running`, `exited`, `backoff`, `crash-looping`, `failed`, `stopping`,
`stopped`, `killed-at-deadline`.

| Where | When | Why |
|---|---|---|
| `/dev/console`, prefixed `ninelives-init ` | from the first instruction | The only channel before `/run` and `$DATA` exist, and what CI reads on the serial port. The `up` record is the ready line #2 waits for. |
| `/run/ninelives/init/state.json` | after `/run` is mounted | A snapshot of current state, replaced with write-and-rename, readable by the diagnostics service (S11). |
| `$DATA/init/records.jsonl` | after `$DATA` is mounted | The history that survives a reboot (S11). Two files of 4 MiB, rotated. Records from before the mount wait in a 1024-record ring, and any beyond that are counted as dropped. |

Each service's stdout and stderr go to `$DATA/init/output/<service>`, two
files of 16 MiB each, rotated, written by the init and owned by root. Before
`$DATA` is mounted, output is held in a 64 KiB buffer per service. Service
output never goes to the console, because a busy service would drown the
console that shows why a boot failed.

The console is opened non-blocking. When the console, the disk or a pipe
cannot keep up, the init drops bytes and records how many. It always keeps
reading the pipes, because a Felix broker blocked on writing a log line is a
stalled log.

None of this needs Felix, which is how state is reported before Felix is
up. Publishing records to Felix is M4.

### Implementation constraints

- One thread runs the event loop (`signalfd`, the pipes, a timeout from the
  nearest deadline). No locks, and fork-safety follows from the child doing
  only system calls. One worker thread runs the calls that can block in the
  kernel: mount, sync, unmount.
- Deadlines use `CLOCK_MONOTONIC`. A clock step from time sync changes no
  decision.
- The decision logic is a pure state machine over events (signal, child
  exit, timer, output) and returns actions. The system call layer executes
  them. This is what makes most invariants testable without a VM.
- `unsafe` lives in `mod sys` ([0005](../decisions/0005-rust-and-unsafe.md)):
  `clone3` and the child's setup, the seccomp load, and whatever rustix does
  not cover. Dependencies: rustix, serde and toml for the config, serde_json
  for the records. Each one gets its sentence in the pull request that adds
  it.

## Invariants

- **I1.** The init never calls `exit` and never returns from `main`. Its only
  way out is `reboot(2)`.
- **I2.** No zombie whose parent is PID 1 exists for more than one loop
  iteration after the `SIGCHLD` that announced it.
- **I3.** Every service process runs, from its first instruction, in its own
  cgroup, and from `execve` on with its configured uid and gid, no
  supplementary groups, an empty ambient set, a bounding set no larger than
  its `capabilities`, `no_new_privs`, its Landlock ruleset and its seccomp
  filter. A child whose setup fails at any step never reaches `execve`.
- **I4.** At most one instance of a service exists. A new one starts only
  after the old one's cgroup is empty.
- **I5.** The n-th consecutive restart of a service waits within ±25% of
  `min(2^(n-1), 30)` seconds, and the count resets only after 60 s of
  continuous running.
- **I6.** No service in phase p starts before every long-running service in
  earlier phases has been started and every `once` service in them has
  exited 0.
- **I7.** After a stop request no service is started or restarted.
- **I8.** A service in phase p receives `SIGTERM` only after every service in
  later phases has exited or its cgroup has been killed.
- **I9.** Each service gets `SIGKILL` (through `cgroup.kill`) no earlier than
  its `stop_timeout_s` after its `SIGTERM`, and the init reaches
  `reboot(2)` within the worst case above.
- **I10.** `reboot(2)` follows a `sync` attempt and an unmount or read-only
  remount attempt of `$DATA`, except where a bound passed, which is
  recorded on the console first.
- **I11.** A service's environment contains exactly its `env` and its
  listed `join_env` names, and it inherits no file descriptor besides 0, 1
  and 2.
- **I12.** What the init runs, as whom, with what privilege, is decided only
  by the config on the read-only root. Nothing on `$DATA`, the command line
  or a signal changes it.
- **I13.** The init never opens a file under `$DATA/credentials/`, and no
  record or output it writes contains a value from `join_env`, which may
  carry the node id but never a secret.
- **I14.** The main loop never waits on the console, a service pipe or the
  data partition. Slow output is dropped and counted.
- **I15.** The init's files on `$DATA` never exceed 8 MiB of records plus
  32 MiB per service of output.
- **I16.** Every state change of the machine or a service produces exactly
  one record, on the console in order.

## Failure modes

PID 1 exiting panics the kernel, which reboots after 10 s. The rows marked
"no" end that way or in a reboot the init chooses; every other row leaves
the init running.

| Failure | Detection | Action | Survivable |
|---|---|---|---|
| A service crashes or exits | `SIGCHLD`, `waitpid` | Restart with backoff; `crash-looping` after 5 | yes |
| A service hangs without exiting | Not detected | Nothing until a stop request (non-goal: health checks). felixd's leases still expire elsewhere if it stops renewing. | yes |
| A service ignores `SIGTERM` | Deadline passes | `cgroup.kill`, recorded as `killed-at-deadline`, shutdown continues | yes |
| A service process stuck in uninterruptible sleep | cgroup not empty 5 s after kill | Recorded, shutdown continues; `reboot(2)` ends it | yes |
| A service forks a daemon or a fork bomb | cgroup still populated; `pids.max` | Contained by `pids.max`; all of it killed with the service | yes |
| A service floods its output | Rotation and drop counters | Output rotated, then dropped and counted; the service never blocks | yes |
| A service exhausts memory | `memory.max`, `memory.oom.group` | The kernel kills the whole service, which restarts | yes |
| Binary, seccomp file or uid invalid | Setup step fails before `execve` | `failed`, machine `degraded`, others keep running | yes |
| felixd starts before Felix is ready | felixd exits 1 | Ordinary restart with backoff | yes |
| The join never succeeds | Non-zero exits or `run_timeout_s` | Retried forever with backoff; role services never start; reported | yes |
| `/proc`, `/sys`, `/dev`, cgroup2 or `/run` fails to mount | `mount` error | `emergency`: no services start, the error repeats on the console every 30 s, signals and reaping still work | yes, by design does nothing else |
| Config missing, unreadable or malformed | Read or parse error | Fail closed: `emergency`, as above. The init never falls back to a default config. | yes, does nothing else |
| Data partition missing, duplicated or unmountable | Not found within `data_wait_s`, two matches, or `mount` error | `degraded`: helpers run, role services do not, retried every 5 s, never formatted | yes |
| Data partition full | Write errors on records and output | Dropped and counted on the console; Felix handles its own `ENOSPC` | yes |
| Console blocked or absent | `EAGAIN`, open failure | Bytes dropped and counted; `/dev/kmsg` used if the console cannot be opened | yes |
| Disk hangs during shutdown | `sync` or unmount bound passes | Recorded on the console, `reboot(2)` anyway; Felix recovers a torn tail | yes |
| Power cut during shutdown | Next boot reads `previous_shutdown: unknown` | Felix recovers as after any crash | yes (the next boot) |
| Clock stepped by time sync | Not needed | Deadlines are monotonic | yes |
| The init's loop hangs (a bug) | Watchdog not petted | Hardware or softdog reset after 60 s | no, reset |
| The init panics | Panic hook | Message on the console, abort, kernel panic, reboot after 10 s | no |
| An allocation in the init fails | Rust aborts | As a panic. The init's memory is bounded by its fixed buffers, and the kernel never OOM-kills PID 1. | no |
| Kernel oops | `oops=panic` | Reboot after 10 s | no |
| Hostile `$DATA/join/env`: unknown names, `LD_PRELOAD`, huge file, newlines, a symlink | Read with `O_NOFOLLOW`, 4 KiB cap, allowlist per service, value checks | Only listed names pass, each value must be printable ASCII under 1 KiB with no `=` or NUL in the name; anything else rejects the whole file, the service does not start, `degraded` | yes |
| Hostile symlink or hardlink planted in a state or output directory | Opened with `openat2(RESOLVE_NO_SYMLINKS \| RESOLVE_BENEATH)`, `fchown` on the fd | The init never follows it, so it cannot be made to chown or write elsewhere; reported | yes |
| A service signals PID 1 | Kernel permission check | Fails: services run as other uids. The kernel also drops signals to PID 1 that have no handler. | yes |
| A service sends a fake `SIGCHLD` or forges exit status | Not possible from another uid | `waitpid` status comes from the kernel, never from the service | yes |
| Hostile words on the kernel command line | Ignored | The init reads neither `argv` nor its environment as PID 1 | yes |
| Hostile `PARTNAME` on an attached disk (a second disk named `ninelives-data`) | Two matches | Neither mounted, `degraded` | yes |

## Security

The threat model ([#56](https://github.com/GetFelix/ninelives/issues/56)) is
not written yet. This section states what it assumes, and each assumption
is listed under [Open questions](#open-questions). A NineLives machine is a
node in a cluster, so one compromised machine is a foothold into it; the
init's job is to make one compromised service stay one compromised service.

### What the init runs as and can reach

The init is root with every capability, because mounting, `clone3` into
cgroups, setting uids and `reboot(2)` need it. It is the only process that
stays root. Its own attack surface is the inputs below, it opens no socket
of any kind and listens on nothing, and it makes no network connection.

### Every input it reads

| Input | Who can write it | Validation |
|---|---|---|
| `/usr/lib/ninelives/init.toml` | The image build only. The root is read-only (S3), and from M3 dm-verity checked against a signed kernel image (S13). | Parsed with unknown keys rejected, checked at image build, size capped. Missing or malformed: fail closed (`emergency`). |
| Kernel command line, `argv`, environment | The bootloader; signed from M3 | Ignored as PID 1. `panic=` and `oops=` are the kernel's, not the init's. |
| Signals | Root, the kernel | Only five meanings, all of them stop or reap. A process running as a service uid cannot signal PID 1. |
| Child exit status | The kernel, from `waitpid` | A pid not in the service table is an orphan, reaped and counted, never trusted as a service. |
| Service stdout and stderr | The service | Treated as opaque bytes: stored, size bounded, never parsed or acted on. |
| Setup-error pipe from a child | The child before `execve` (still init's code) | Close-on-exec, so it is closed before service code runs; reads are a fixed-size integer. |
| `/sys/class/block/*/uevent` | The kernel, from partition tables anyone with the disk can write | Only `PARTNAME` is read, exact match, duplicates refused. |
| `cgroup.events` | The kernel | Only `populated`. |
| `$DATA/join/env` | The join | See the hostile input row above. Never follows symlinks, capped, allowlisted. |
| `$DATA/join/node-id` (host name) | The join | Host name characters only, 64 bytes, else `default_hostname`. |
| `$DATA/init/records.jsonl` (previous boot) | Only the init (root, 0700) | Read only to report `previous_shutdown`; a line that does not parse reads as `unknown`. |

### Config integrity

What runs, as whom and with what privilege comes only from the image (I12).
`$DATA/join/env` is the one input from writable state, and it is data, not
config: it supplies values for names the image already chose, for the one
service the image named, and it cannot add a service, change a uid, a
capability, a filter or a path the init touches. A compromised join could
point felixd at hostile brokers, but it could do that anyway through the
credentials it writes; the init adds no new power. Removing even this path
needs felixd to read these settings from a file itself, which 0.1.0 does
not do.

### What each service keeps

| Service | uid | Capabilities after start | Why |
|---|---|---|---|
| time (chronyd) | `_chrony` | `CAP_SYS_TIME` | It sets the clock. It starts as root with its bounding set limited to `CAP_SYS_TIME`, `CAP_SETUID` and `CAP_SETGID`, and drops to `_chrony`. No ambient set, so S6 holds after its own drop. |
| network (dhcpcd) | `dhcpcd` (privilege separation) | `CAP_NET_ADMIN`, `CAP_NET_RAW`, `CAP_NET_BIND_SERVICE` in its privileged helper | It configures interfaces and binds port 68. Same start-as-root rule. |
| join | own | none | |
| felix-controlplane, felix-broker | own each | none | Their ports are above 1024. |
| felixd | own | none | [felixd-contract.md](../felixd-contract.md#running-it): needs no root and no capabilities. |

Every service also gets `no_new_privs`, its seccomp filter, its Landlock
ruleset, its cgroup limits, `/proc` with `hidepid=invisible`, a read-only
root, and write access only to its own state directory (S6). io_uring, which
bypasses seccomp, is compiled out of the kernel.

### Escalation through the init

- **Signals.** A service cannot signal the init (different uid) or another
  service (different uids). The init signals only its own direct children by
  pid, and only before reaping them. A pid cannot be reused until its parent
  reaps it, and the init is that parent, so a kill can never hit a recycled
  pid. That is why the init does not need pidfds; a pidfd would only add an
  fd to track. Everything beyond the main process is killed through
  `cgroup.kill`, which names the cgroup, not a pid.
- **cgroups.** Service cgroups are owned by root, so a service cannot move
  itself out of its limits or into another's group.
- **File descriptors.** Every init fd is `O_CLOEXEC`, and the child closes
  everything above 2 before `execve` (I11).
- **Environment.** Built from nothing per service (I11).
- **Files the init writes for a service.** State directories are created and
  chowned through fds opened without following links. Output and record
  files live under `$DATA/init/`, root-owned 0700, which no service can
  write.

### Secrets

The init never reads credentials and never holds one (I13, S8). The join
writes credentials to `$DATA/credentials/<service>/`, a directory the init
creates owned by that service's uid, mode 0700. felixd gets only a path,
`FELIX_AUTH_TOKEN_FILE`, from the image config, and reads the token itself
on each use ([felixd-contract.md](../felixd-contract.md#running-it)). So the
token never passes through the init, its command line or its environment.
Service output may contain whatever a service prints; keeping secrets out of
its output is each service's obligation under S8, and the S8 test checks it.

### Requirements

S1 (no shell, no network listener, helpers chosen to need none), S3 (the
init writes only `/run` and `$DATA`), S6 (the start sequence above), S8
(secrets), S11 (state and output kept on `$DATA` with a size bound; the
authenticated interface that reads them is a separate service), N3 and N4
(cgroup reservation, numbers from measurement).

## Test plan

Unit tests run the state machine with a fake clock and fake events, and
cover most invariants without a kernel. qemu tests boot the CI image
([#2](https://github.com/GetFelix/ninelives/issues/2)). Fault tests run on
the schedule in [#50](https://github.com/GetFelix/ninelives/issues/50).

| Test | Kind | Proves |
|---|---|---|
| Event sequences never produce an exit action; every error path returns to the loop | unit, property | I1 |
| A static check that `std::process::exit` and a returning `main` appear only in the local command path | CI | I1 |
| A test service forks 1000 children and exits; no zombie under PID 1 after one iteration | qemu | I2 |
| Each service's `/proc/<pid>/status` (Uid, Gid, Groups, CapAmb, CapBnd, NoNewPrivs, Seccomp) and `/proc/<pid>/cgroup` match the config | qemu | I3, S6 |
| Fault injected at each child setup step: the service never execs, the step is reported | unit (sys mocked), qemu | I3 |
| A service exits leaving a child in its cgroup; the next instance starts only after the cgroup empties | qemu | I4 |
| Backoff schedule, jitter bounds and the 60 s reset | unit | I5 |
| Phase order with a `once` service that fails twice | unit, qemu | I6 |
| A service crashes during shutdown and is not restarted | unit, qemu | I7 |
| Shutdown order and `SIGTERM` times recorded against the config | unit, qemu | I8 |
| A service that ignores `SIGTERM` is killed no earlier than its deadline and the machine still powers off within the worst case | qemu, fault | I9, #48 |
| Power-off with a hung data disk (qemu blockdev throttled to zero): `reboot(2)` reached after the bounds | fault | I10 |
| A test service dumps its environment and `/proc/self/fd` | qemu | I11 |
| `$DATA/join/env` planted with `LD_PRELOAD`, a symlink, an oversized file and binary bytes: the service does not start or gets only allowlisted names | qemu | I12, hostile inputs |
| A symlink planted in a state directory pointing at `/run/ninelives`: nothing outside is chowned or written | qemu | hostile inputs |
| Known test credentials planted; the console, records, output and `/run` searched for them | qemu | I13, S8 |
| A service writes 1 GB to stdout as fast as it can while the console is throttled: the loop still pets the watchdog and other services restart on time | fault | I14 |
| Disk full and output flood: `$DATA/init` stays within its bound | fault | I15 |
| Records cover every transition in the unit event traces; console order matches | unit, qemu | I16 |
| A service running as its uid sends every signal to PID 1 and to another service: all fail | qemu | escalation |
| Kernel command line with extra words and `KEY=VALUE` pairs boots identically | qemu | hostile inputs |
| A second disk with a partition named `ninelives-data` | qemu | hostile inputs |
| Malformed and missing config: the image build fails (`init check`); booted anyway, the machine reports `emergency` and still powers off on request | CI, qemu | fail closed |
| After a clean power-off, `felixctl data inspect segments` on the data disk reports nothing to repair, and the next boot reports `previous_shutdown: clean` | qemu | 0002's durability check |
| Power cut during shutdown, then boot: `previous_shutdown: unknown`, Felix starts | fault | power loss row |
| The init loop made to hang (test build only): the watchdog resets the VM | fault | watchdog row |

## Open questions

| Question | Proposal | Decides | Needed by |
|---|---|---|---|
| **Felix's stop contract.** What Felix's broker and control plane guarantee on `SIGTERM` (everything acknowledged written and logs closed before exit 0, in every fsync mode, including the control plane's Raft store), and their worst-case stop time. Without it, a clean exit is evidence and the outside-the-VM check is the proof. | A `docs/felix-contract.md` page, like felixd's, backed by Felix docs or an issue filed in Felix; deadlines set from it | NineLives maintainers with Felix | M1 (#48) |
| Whether `felixctl` ships in the Felix release tarball and whether anything inspects the control plane's store the same way | Check 0.7.2's artifact; file a Felix issue for what is missing | NineLives maintainers | M1 |
| A readiness signal from Felix, so felixd's early exits are not counted as restarts | Live without it; revisit if boot time suffers | NineLives maintainers | M2 |
| **Power button and cloud stops.** qemu `system_powerdown` and most clouds send an ACPI power button event, not a signal. Handling it means reading the power-button input device, which 0002 does not list. Some clouds also allow less than our worst case before a hard stop. | Amend 0002 to add the power button; set a stop budget per cloud target with felixd drain | NineLives maintainers | M5; CI uses Ctrl-Alt-Del with qemu `-no-reboot` until then |
| DNS: with dhcpcd's hooks off, nothing writes `/etc/resolv.conf` (a link into `/run`). Also confirm dhcpcd 10 runs with no hook script. | dhcpcd writes it through a minimal non-shell hook, or the join records brokers by address | DHCP and join designs (#5) | M1 |
| Who loads the packet filter (S7), and that it loads before the network service | A `once` service in phase 1 that loads a static ruleset from the image | Network design | M1 |
| How the seccomp and Landlock profiles are produced and reviewed | Recorded from qemu runs, minimised by hand, compiled at image build, reviewed per service bump | Image design | M1 |
| Whether Felix waits for time sync, since certificate checks need a sane clock | No wait; qemu and clouds have an RTC; revisit with the threat model | Threat model (#56) | M1 |
| **Threat model assumptions** this page makes: the image is trusted once built; the data partition is not trusted for what runs but is trusted not to be swapped while mounted; root on the machine is game over; physical access is out of scope until M3's verified boot; a compromised service is the main attacker. | Confirm or correct in #56 | Threat model (#56) | M1 |
| Whether `emergency` should reboot so M3's boot counting can fall back to the other image | Stay up and report until M3; M3 decides | A/B design (#8) | M3 |
| cgroup numbers (`memory.min`, `cpu.weight`) | From the N4 measurement | NineLives maintainers | M2 |
