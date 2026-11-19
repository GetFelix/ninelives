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
kernel.org. The build checks the release's PGP signature against the
stable maintainers' key fingerprints pinned in `kernel/keys/`, and fails if it
does not verify. No patches. The config is a small upstream base plus fragments in `kernel/`,
each commented with the requirement it serves:

- **Hardening:** what [kernel-hardening-checker](https://github.com/a13xp0p0v/kernel-hardening-checker)
  recommends, with each exception listed and explained in `kernel/`
  (requirement S2), lockdown in integrity mode, unprivileged BPF and
  unprivileged userfaultfd off, BPF JIT hardening on.
- **No loadable modules.** Everything the image needs is built in, so
  nothing can be loaded at run time. Each target's drivers (virtio for
  qemu, then each cloud and board in M5) are listed in that target's
  fragment. Firmware those drivers need comes from Debian's firmware
  packages, pinned like every other package ([0006](0006-debian-base.md)).
- **Networking:** what Felix's QUIC stack uses, UDP segmentation and
  receive offload (GSO and GRO) on each target's network driver
  (requirement N2). QUIC's congestion control runs in Felix's process, so
  TCP congestion control and qdisc choices go in only with a measurement
  (N3). io_uring, eBPF and XDP are compiled out until a measured need and a
  decision record bring one in, because io_uring bypasses seccomp (S6).
  Every other subsystem a NineLives machine does not use is compiled out.
- **Sandboxing for the init's services:** seccomp, Landlock, cgroup v2,
  and namespaces.

Which LTS release, and how a bump is tested, are settled in #3. Every
kernel change runs the image's boot, durability and network tests
([requirements.md](../requirements.md)).

### Amended by the kernel's design, 2026-11-18

[kernel.md](../components/kernel.md) sets lockdown to confidentiality mode and compiles out the BPF syscall and userfaultfd, instead of integrity mode and the unprivileged switches above.

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
  built in, or support that is not in mainline yet. The Raspberry Pi (M5)
  is the likely first case.
