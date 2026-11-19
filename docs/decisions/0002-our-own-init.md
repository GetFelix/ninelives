# 0002: PID 1 is our own init, written in Rust

**Status:** Accepted, 2026-11-18. Holds through M2, reviewed again at M4
([#15](https://github.com/GetFelix/ninelives/issues/15)).

## Context

M0 boots with whichever init the image contains. M1 is where the choice
matters: the first-boot join, starting each role's services and keeping
them apart all hang off PID 1. M4 moves supervision decisions onto the log.

The init is the piece of the operating system closest to what NineLives is
about. Starting services, restarting them and reporting their state is the
same job felixd does for workloads, one level down.

## Decision

PID 1 is a small init of our own, written in Rust, in `init/`. It does only
what a NineLives machine needs:

1. Bring the machine up: mount `/proc`, `/sys`, `/dev` (devtmpfs), the
   cgroup v2 hierarchy and the filesystems the image declares, set the host
   name, bring up loopback, and wait for the data disk to appear.
2. Reap orphaned processes.
3. Start the services for the machine's role in dependency order: the
   machine's helpers (network configuration, time sync), then the Felix
   control plane and broker where the role has them, then felixd. Each runs
   under its own user and in its own cgroup, with its output captured.
4. Restart a failed service with backoff.
5. Handle requests to stop: reboot, power off, and the kernel's
   Ctrl-Alt-Del, from signals or a local command.
6. Shut down in order: stop services in reverse start order, give each the
   time its contract says it needs (felixd's is about 130 s with its
   default settings, see [felixd-contract.md](../felixd-contract.md)), kill
   what is still running at its deadline, then `syncfs` the data partition
   and power off or reboot. A service killed at its deadline is reported,
   and the shutdown still finishes.
7. Report each service's state.

Network configuration and time sync are small services the init starts,
not part of PID 1. Time sync matters: Felix and felixd leases are measured
in seconds. The init's design says which programs provide them, how the
hardware watchdog and kernel panic settings are used, and what PID 1 does
on each error it can and cannot survive.

Anything outside that list needs a design change that argues for it. The
init's design (scope, invariants, failure modes, test plan) is written and
reviewed before its code
([#46](https://github.com/GetFelix/ninelives/issues/46)).

The init talks to the kernel through standard system calls and the cgroup
v2 filesystem, so it does not tie NineLives to Linux more than
[0001](0001-linux-kernel.md) already does.

### Amended by the init's design, 2026-11-18

The design review changed the list in four places:

- **Launchers.** Before dropping root, PID 1 forks one small process per
  service at that service's uid, which starts the service when PID 1 asks.
  This is the price of no root after early boot: starting processes under
  several uids from PID 1 would need `CAP_SETUID`, which is root in all but
  name.
- **`syncfs` instead of unmount** (item 6). `syncfs` on an fd opened at
  mount reports every writeback error since the mount, because the kernel
  samples errors when a file is opened. An unmount fails with `EBUSY` behind
  a stuck process and needs `CAP_SYS_ADMIN` in PID 1.
- **The power button** (item 5). It is how qemu and every cloud ask a guest
  to stop; without it every cloud stop is a power cut instead of an ordered
  shutdown.
- **The packet filter** is not part of PID 1. A separate loader runs once
  in phase 0, before the drop, like the helpers in item 3; the init only
  starts it earlier, so no running service needs netfilter access.

## Options considered

| Option | Why not |
|---|---|
| systemd | It does far more than a NineLives machine needs, and much of that (supervision, ordering, restart policy) is what NineLives means to move onto the log. Its M3 tools (sysext, repart, boot counting) are real losses; boot counting is a file rename we can do ourselves, and Felix upgrades ride with the image instead of sysext. It also only runs on Linux, which would make every later kernel experiment carry it. |
| s6, runit or dinit | Small and proven, but we would still write the role logic, join and reporting around them, and we would be adapting our model to theirs instead of the other way round. |
| felixd as PID 1 | felixd depends on Felix, and Felix has to be running first. It inverts the bootstrap. |

## Consequences

- We own the hardening: cgroup setup, restart policy, shutdown ordering and
  the init's own failure handling. If PID 1 exits the kernel panics, so the
  init must never exit on an error it can survive, and its design has to say
  what happens on every one it cannot.
- **Shutdown is a durability path.** If the init powers off before Felix
  has flushed, every reboot looks like a crash. Felix still recovers, but a
  bug here is ours. CI checks that Felix starts after a clean shutdown with
  nothing to repair. Power loss and a service killed at its deadline still
  happen, and those are Felix's recovery to handle, as on any machine.
- Networking comes from the kernel command line in qemu and from a DHCP
  client the init starts later. Which client is part of the init's design.
- M1 takes a few weeks longer than it would on systemd.
- Service state can be reported as records from the start, which is where
  M4 wants it.

## Reopen when

- The init's design review finds that the scope above cannot be met safely
  without growing it into a general service manager.
- M4, when supervision decisions move onto the log and the init's job
  shrinks or changes.
- A milestone needs a systemd feature with no small equivalent.
