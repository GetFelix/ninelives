# 0007: The kernel is built from upstream source with our own config

**Status:** Accepted, 2026-11-18
([#3](https://github.com/GetFelix/ninelives/issues/3)).

## Context

[0001](0001-linux-kernel.md) chose stock Linux. That leaves where the
kernel binary comes from: the base distribution's package, or a build of
the upstream source.

A distribution kernel is built to boot anything. It ships thousands of
modules for hardware and features a NineLives machine never uses, and its
config is a compromise across every user of the distribution. NineLives
machines do one job, and two requirements in
[requirements.md](../requirements.md) depend directly on the kernel config:
a small attack surface, and a network stack tuned for Felix's traffic
(QUIC over UDP, many connections, durable writes).

## Decision

The kernel is built in CI from an upstream long-term-support release from
kernel.org, with the tarball's signature verified before the build. No
patches. The config is a small upstream base plus fragments in `kernel/`,
each commented with the requirement it serves:

- **Hardening:** the Kernel Self Protection Project's recommended options
  where they do not break a requirement, lockdown in integrity mode,
  unprivileged BPF and unprivileged userfaultfd off, BPF JIT hardening on.
- **No loadable modules.** Everything the image needs is built in, so
  nothing can be loaded at run time. Each target's drivers (virtio for
  qemu, then each cloud and board in M5) are listed in that target's
  fragment.
- **Networking:** UDP segmentation and receive offload (GSO and GRO, which
  QUIC batching uses), BBR with the fq qdisc, io_uring, and eBPF and XDP
  for later use. Subsystems a NineLives machine does not use are compiled
  out.
- **Sandboxing for the init's services:** seccomp, Landlock, cgroup v2,
  and namespaces.

Which LTS release, and how a bump is tested, are settled in #3. Every
kernel change runs the image's boot, durability and network tests
([requirements.md](../requirements.md)).

## Options considered

| Option | Why not |
|---|---|
| Debian's kernel package | Thousands of modules we would never load, and a config we do not control. It would also tie the kernel's version to the base distribution's release. |
| A distribution kernel with modules stripped after install | Still someone else's config, and the hardening and networking options we need may not be set at all. |

## Consequences

- We build and own the kernel config, and follow upstream LTS security
  releases ourselves. A kernel security release triggers a rebuild within
  the deadline in the requirements.
- The kernel is the main GPL component we build, so publishing images means
  publishing its exact source and config, which the build already has.
- New hardware needs a config change and a new image, never a module.
- Kernel builds add CI time. They are cached by config and version.

## Reopen when

- Following LTS security releases ourselves turns out to be slower than
  the deadline in the requirements.
- A target (a cloud, a board) needs drivers that cannot reasonably be
  built in.
