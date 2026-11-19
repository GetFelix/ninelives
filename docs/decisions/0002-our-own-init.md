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

1. Mount the filesystems the image declares.
2. Reap orphaned processes.
3. Start the services for the machine's role (the Felix control plane and
   broker where the role has them, then felixd), each under its own user and
   in its own cgroup.
4. Restart a failed service with backoff.
5. Shut down in order: stop services in reverse start order, wait for each
   with a deadline, `sync`, unmount, then power off or reboot.
6. Report each service's state.

Anything outside that list needs a design change that argues for it. The
init's design (scope, invariants, failure modes, test plan) is written and
reviewed before its code
([#46](https://github.com/GetFelix/ninelives/issues/46)).

The init talks to the kernel through standard system calls and the cgroup
v2 filesystem, so it does not tie NineLives to Linux more than
[0001](0001-linux-kernel.md) already does.

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
  nothing to repair.
- Networking comes from the kernel command line in qemu and from a small
  DHCP client later. It is not part of the init.
- M1 takes a few weeks longer than it would on systemd.
- Service state can be reported as records from the start, which is where
  M4 wants it.

## Reopen when

- The init's design review finds that the scope above cannot be met safely
  without growing it into a general service manager.
- M4, when supervision decisions move onto the log and the init's job
  shrinks or changes.
- A milestone needs a systemd feature with no small equivalent.
