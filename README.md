<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/brand/horizontal-dark.svg">
    <img src="docs/brand/horizontal-light.svg" alt="NineLives" width="520">
  </picture>
</p>

# NineLives

A distributed operating system built on the [Felix](https://github.com/GetFelix/felix) log.

> **Status:** design stage. Nothing boots yet. The plan is in
> [docs/design.md](docs/design.md) and the work is tracked in
> [milestones](https://github.com/GetFelix/ninelives/milestones).

## What it is

NineLives treats a cluster as one machine. Every change to the system is a
durable, ordered record in a Felix log: what should run, where it runs, what
each program received and what it produced. A program's state can be rebuilt
from that log on any machine, so when a machine dies its programs carry on
somewhere else. That is where the name comes from.

The pieces:

- **A bootable image** for each machine: a pinned, stock Linux kernel, a
  read-only root and atomic A/B upgrades.
- **[felixd](https://github.com/GetFelix/felixd)**, the node agent that runs
  workloads from the log. It has its own repo; this one ships it.
- **Felix brokers** on some of the machines.
- **System programs on the log:** the scheduler, the name service and the
  `fx` shell.

Linux handles drivers, memory and local processes. NineLives handles what
Linux can't do across machines: processes that outlive a machine, pipes and
files that are durable streams, and a history you can replay.

## Layout

| Directory | What it holds |
|---|---|
| `boot/` | Bootloader configuration (systemd-boot), UEFI and secure-boot signing |
| `kernel/` | Linux kernel config fragments and the pinned version. No fork. |
| `felixd/` | The node agent, as a git submodule pinned to a felixd commit |
| `init/` | Early boot: mounts, network, first-boot cluster join, starting felixd |
| `rootfs/` | The image definition: read-only root, A/B partitions |
| `system/` | Programs that run on the log: scheduler, name service, `fx` shell |
| `tools/` | The `fx` CLI, image build wrappers, flashing, the qemu runner |
| `images/` | Build targets: qemu x86_64 and aarch64, cloud images, Raspberry Pi |
| `docs/` | Design, boot sequence, threat model |

Images are built with [mkosi](https://github.com/systemd/mkosi).

## Building

Clone with `git clone --recurse-submodules`, or run
`git submodule update --init` in an existing clone.

Nothing builds yet. The first milestone produces an image that boots in qemu, and the
third boots three of them into a cluster that survives losing one.

## The logo

A cat whose head and tail make a 9. The tail is the tail of the log: nine
records, one of them lost after a machine died, and the newest glowing amber
like the eyes. The files and how to regenerate them are in
[docs/brand](docs/brand/).

## License

MIT for everything in this repository. Built images also contain the Linux
kernel and other GPL software, which keep their own licenses; see
[docs/design.md](docs/design.md#licensing).
