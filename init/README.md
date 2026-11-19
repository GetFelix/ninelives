# init

The NineLives init, PID 1, in Rust ([decision 0002](../docs/decisions/0002-our-own-init.md)).
The design is [docs/components/init.md](../docs/components/init.md). This is
its first slice ([#47](https://github.com/GetFelix/ninelives/issues/47)):
enough for the M0 image to boot, report that it is up, reap orphans and stop
cleanly. It starts no services.

After early boot PID 1 is not root (S24, [#68](https://github.com/GetFelix/ninelives/issues/68)):
it is uid and gid 900 (`init` in `images/users.toml`) and keeps only
`CAP_KILL` and `CAP_SYS_BOOT`, permitted. Its seccomp filter is not in yet.

## What it does

The binary is `init`; the image installs it as `/usr/lib/ninelives/init`
and the kernel starts it with `init=`. In order:

1. Blocks every signal, so a stop request sent during boot is kept for the
   loop instead of being discarded.
2. Mounts `/dev` (devtmpfs, `nosuid,noexec`) and opens its own
   non-blocking handle on the console, `/dev/kmsg` if there is none. Fds 0
   to 2 are the console the kernel opened; any that is closed, on a machine
   with no console device, is filled so later descriptors cannot land there.
3. Mounts `/proc` (`hidepid=invisible`), `/sys`, `/sys/fs/cgroup` (cgroup2,
   `nsdelegate,memory_recursiveprot`), `/run` (`mode=0755,size=16m`) and
   `/tmp` (`mode=1777,size=64m`), all `nosuid,nodev,noexec`. The first
   failure stops the mounts and the machine goes `emergency`.
4. Bind-remounts `/proc/sys` read-only whenever `/proc` is mounted, even
   after a later mount failed, so no later process can change a sysctl. A
   failure here is also `emergency`.
5. Turns Ctrl-Alt-Del into `SIGINT` (`reboot(LINUX_REBOOT_CMD_CAD_OFF)`).
6. Drops root ([Privilege](../docs/components/init.md#privilege)), in this
   order: securebits `NOROOT` and `NO_SETUID_FIXUP`, both locked; the
   bounding set cut to `CAP_KILL` and `CAP_SYS_BOOT`; the ambient set
   emptied; `setgroups([])`, `setresgid` and `setresuid` to 900; `capset` with
   permitted `CAP_KILL` and `CAP_SYS_BOOT` and nothing else; not dumpable;
   `no_new_privs`. It then reads all of that back from the kernel and writes
   the drop record. Any failing step or any value the kernel does not
   confirm powers the machine off: nothing runs with a root PID 1. The drop
   happens in `emergency` too.
7. Opens a signalfd, starts the worker thread (after the drop, so it
   inherits it), writes the ready line and enters the event loop.

The loop reaps every exited child on `SIGCHLD` (I2), records and ignores
signals it has no use for, and on a stop request runs the final steps of
the ordered shutdown: `sync()` on the worker thread, bounded at 30 s, then
`reboot(2)`. If no thread can run the sync, it is skipped and recorded
rather than run unbounded on the loop. `SIGTERM` and `SIGINT` reboot, `SIGUSR2` powers off, and a
request during shutdown can only turn a reboot into a power-off. In
`emergency` the machine runs nothing else, repeats the reason every 30 s,
and still reaps and stops. If the signalfd cannot be set up, the loop could
never hear a stop request, so it reports that and powers off.

PID 1 never exits (I1): the entry point has type `!`, the loop's only way
out is `reboot(2)`, a failed `reboot(2)` is recorded and retried, and the
build sets `panic = "abort"` with a hook that puts the message on the
console first. The one `exit` is for a binary started as anything but
PID 1, which refuses to run rather than mount over a host's `/dev`.

### The ready line

Records go to the console, one JSON object per line behind the prefix
`ninelives-init `. The qemu boot check
(the `boot` job in CI) waits for the first
line that starts with `ninelives-init {` and contains `"machine":"up"`:

```
ninelives-init {"v":1,"boot":"0f6c2a3e-6d3c-4c58-9c55-6a4b1f3e2d10","mono_ms":1234,"wall_ms":1763500000000,"machine":"up"}
```

The fields are in [init.md](../docs/components/init.md#state-reports).

Just before it, the drop record, which the boot check reads. Its sets are
hex as `/proc/<pid>/status` prints them; `securebits` is the kernel's number
(15 is both bits, both locked), which `status` does not show:

```
ninelives-init {"v":1,...,"machine":"booting","step":"drop root","result":"ok","uid":[900,900,900],"gid":[900,900,900],"groups":0,"cap_prm":"0000000000400020","cap_eff":"0000000000000000","cap_inh":"0000000000000000","cap_amb":"0000000000000000","cap_bnd":"0000000000400020","securebits":15,"no_new_privs":1}
```

`scripts/boot_image.py` fails the boot if the record is missing or any field
differs. The values come from the kernel by system calls; the inspector that
reads `/proc/1/status` as well is [#98](https://github.com/GetFelix/ninelives/issues/98).

## What it does not do yet

| Part of init.md | Where |
|---|---|
| PID 1's own seccomp filter (the last step of the drop) | [#68](https://github.com/GetFelix/ninelives/issues/68) follow-up |
| The config (`init.toml`, step 2) and its `[[mount]]` entries; `/tmp` is fixed in the mount table until then | [#48](https://github.com/GetFelix/ninelives/issues/48) |
| `gid=nl-inspect` on `/proc`, which needs the config's group | [#48](https://github.com/GetFelix/ninelives/issues/48) |
| The data partition, its `syncfs` at shutdown, records on `$DATA` | [#4](https://github.com/GetFelix/ninelives/issues/4), [#48](https://github.com/GetFelix/ninelives/issues/48) |
| Directories, the cgroup tree, host name, loopback, rlimits (steps 5 to 7) | [#48](https://github.com/GetFelix/ninelives/issues/48) |
| Launchers, services, restart, phases, the ordered shutdown of services | [#48](https://github.com/GetFelix/ninelives/issues/48) |
| The power button (`KEY_POWER`) | [#86](https://github.com/GetFelix/ninelives/issues/86) |
| The watchdog, the `/dev/kmsg` reader, the request FIFO, `/run/ninelives/init/state.json` | [#48](https://github.com/GetFelix/ninelives/issues/48) |

## Layout

| Module | What |
|---|---|
| `machine` | The decision logic: a pure state machine from events to actions. |
| `boot_plan` | The order of the boot steps and what a failure stops, over a `BootOps` trait. |
| `event_loop` | The loop, over an `Os` trait so it runs against a fake in tests. |
| `signals`, `mounts`, `record` | The signal table, the mount table, the record encoder and sink state. Pure. |
| `privilege` | The drop's steps, order and the state the kernel must confirm; `privilege/linux.rs` makes the calls. |
| `boot`, `host`, `console`, `clock` | Linux only: the boot system calls, the real `Os`, the console and panic hook. |
| `sys` | Every `unsafe` in the crate: the C entry point, the signal mask and `signalfd`. |

## Dependencies

- **rustix**: the system calls (mount, waitid, reboot, poll, eventfd, sync)
  without `unsafe` in this crate, as [0005](../docs/decisions/0005-rust-and-unsafe.md) asks.
- **libc**: `sigfillset`, `pthread_sigmask`, `signalfd`, `getresuid` and `getresgid`, which rustix
  does not wrap. glibc's versions also keep the signals glibc reserves for
  its threads out of the blocked set.

Both are Linux-only dependencies; the pure modules build without them.

## Build and test

```bash
cargo build --release          # Linux; the binary is target/release/init
cargo test                     # any OS: unit tests; on Linux also tests/pid1.rs
cargo clippy --all-targets -- -D warnings
cargo deny check
python3 ../scripts/check_unsafe.py .   # unsafe only in mod sys
```

The unit tests cover the signal table, the mount table (read against
init.md itself), the boot order and its fail-closed rules, the record
format, the privilege drop (every step fatal, every field checked), drop counting and torn lines, the capped panic line, the state
machine and the loop, including random event sequences that check I1, I2
and I10. On Linux, `tests/pid1.rs` runs the real binary as PID 1 of
a new PID and mount namespace through `sudo unshare` and checks the ready
line, the mounts and their options, the read-only `/proc/sys`, orphan
reaping, the exit through `reboot(2)` for each stop signal, a failed
mount that leaves the machine `emergency` with `/proc/sys` still locked, and
that PID 1 is uid 900 with exactly the two capabilities in `/proc/<pid>/status`.
Not yet tested end to end: the panic hook firing and the signalfd and worker
failures, which need the test image's fault hooks. Without
passwordless sudo those tests skip; CI sets `NL_INIT_ROOT_TESTS=1` so they
fail instead. CI boots the image in qemu (the `boot` job) and waits for the ready line.
