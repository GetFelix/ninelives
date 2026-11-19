# 0001: The kernel is stock Linux

**Status:** Accepted, 2026-11-18. Holds through M2, then reviewed
([#3](https://github.com/GetFelix/ninelives/issues/3),
[Discussion #22](https://github.com/GetFelix/ninelives/discussions/22)).

## Context

M0 needs a kernel to boot. M1 and M2 need one that runs the Felix broker and
control plane, which ship only as Linux binaries, and felixd, which ships for
Linux x86_64 and aarch64.

Nothing in M0 to M2 needs a kernel feature of its own. What NineLives adds
(workloads that outlive a machine, leases and fencing, replay) sits above the
kernel, in felixd and Felix. The kernel's job through M2 is to run them
without surprises.

The one kernel property that cannot be negotiated is storage. Felix's
recovery assumes `fsync`, `pread` and preallocation behave as documented on
Linux. A kernel that gets any of that subtly wrong loses records Felix has
already acknowledged, which breaks the one promise the whole system rests on.

## Decision

NineLives runs stock Linux: an upstream long-term-support release, pinned to
an exact version, with no patches and no out-of-tree modules. Which release,
whether it is built from source or taken from the base distribution, and the
config fragments are settled in #3.

Everything NineLives writes uses standard system calls only, so the kernel
can be replaced without rewriting the system above it. That rule is enforced
by CI, not by good intentions
([#49](https://github.com/GetFelix/ninelives/issues/49)).

## Options considered

| Option | Why not now |
|---|---|
| A Linux-compatible Rust kernel (Asterinas) | It would have to earn Felix's trust in `fsync` and preallocation first, and how much of what real programs use it covers is unknown. Plan 9's POSIX layer fell short in exactly that way. Worth trying, not worth depending on. |
| A unikernel (Hermit) | No general-purpose userland, so no native tier for existing programs. tokio and quinn need porting before Felix runs at all. |
| A kernel of our own | Boot, paging and a scheduler are weeks of work. Drivers, a network stack, storage semantics, security fixes and multicore are years, and none of it is what NineLives is for. |

## Consequences

- M0 is days of work, not months, and the rest of the project builds on a
  kernel with known behaviour.
- The trusted base is large, and nothing in Linux is shaped around the log.
  We accept that through M2.
- Published images contain GPL software, so publishing images means
  publishing the matching source for it
  ([Licensing](../design.md#licensing)).
- Because NineLives code stays on standard system calls, booting the same
  root on another kernel is an experiment, not a port. After M2, CI boots
  the image on Asterinas without blocking anything, and records what fails
  ([#54](https://github.com/GetFelix/ninelives/issues/54)).

## Reopen when

- M2 passes. This record is reviewed then whatever else happens, with the
  results of the Asterinas experiment in hand
  ([#53](https://github.com/GetFelix/ninelives/issues/53)).
- A feature NineLives needs (deterministic scheduling for replay, a
  log-aware I/O path) cannot be built on Linux without patching it.
- Linux's behaviour under one of Felix's durability assumptions changes.
