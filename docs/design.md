# NineLives design

## Goal

A cluster that behaves like one machine, where the system's memory is a
replicated log. Any program's state can be rebuilt from the log, so failover,
migration and time travel are the same operation.

[felixd's design](https://github.com/GetFelix/felixd/blob/main/docs/design.md)
covers how workloads run, hold leases, write exactly-once output and replay.
This document covers the operating system around it: what boots on each
machine, how a machine joins, and which system services live on the log.

Very little of this is decided. The kernel, the init system, how images are
built and how machines are upgraded are all open questions, listed at the
end. Where this document names a tool, it is the current proposal, written
down so it can be argued with.

## What runs where

Every machine runs the NineLives image: a kernel, early init and felixd. Three or
five core machines also run the Felix control plane and a broker, and storage
machines run a broker. Felix is the one layer the system does not schedule,
because everything else depends on it. Roles, services and how a cluster comes
up are in [topology.md](topology.md).

| OS concept | In NineLives |
|---|---|
| Process | A workload: a module plus its input streams. Its state is a fold over its inputs. |
| PID | A workload id, independent of the machine running it |
| Pipes and IPC | Streams, durable and across machines |
| Files | Append-only streams. A mutable file is a workload that folds edits into contents, so old versions come for free. |
| Namespace | A tree of stream and cache names, with per-user views as in Plan 9 |
| Scheduler | A workload that reads heartbeats and demand and writes assignments, so its decisions can be replayed |
| Users and permissions | Felix tenants and grants. A program's token is its capability. |
| Boot | Read the desired state and claim leases. A cold start of the cluster is a replay. |
| Upgrades | Machines: an image swap that can roll back (A/B partitions are the proposal). Workloads: run the new module over recorded inputs, compare, switch at an offset. |

## The kernel

Undecided, with no proposal yet. The options are stock Linux, a kernel of our
own, or an existing Rust kernel such as one that is compatible with Linux at
the syscall level. The trade-off is being discussed in
[Discussion #22](https://github.com/GetFelix/ninelives/discussions/22).
Either way, nothing above the kernel should depend on kernel-specific features
more than it has to.

## The image

What the image must do is settled more than how. The current proposal:

- Built with mkosi from `rootfs/` and `images/<target>/`. Proposed, not chosen.
- systemd-boot, unified kernel images, and secure boot once signing exists.
  Proposed, and tied to the kernel and init questions.
- A read-only root and two root partitions. An upgrade writes the inactive
  one and switches on reboot; a failed boot falls back. The update mechanism
  is open; A/B partitions are the proposal.
- Machine state that must survive an upgrade (Felix data, the machine's
  identity and credentials) lives on a separate data partition.

## Boot sequence

This is the sequence under the current proposal; the steps that name a tool
change if the proposal does.

1. Firmware loads the bootloader (systemd-boot is proposed), which loads the kernel.
2. Early init mounts the root and data partitions and brings up the network.
3. On first boot, the machine joins a cluster: it reads a join token from
   cloud metadata, a config partition or the kernel command line, registers,
   and stores its credentials on the data partition.
4. The control plane starts on core machines and brokers on core and storage
   machines, as services under the init system ([topology.md](topology.md)).
5. felixd starts, connects to Felix and claims the work the log assigns it.

The proposal is systemd as PID 1 for the first milestones. Whether NineLives
needs its own init is an open question
([#15](https://github.com/GetFelix/ninelives/issues/15)); it depends on how
much of systemd's job moves onto the log.

## Milestones

| | What works at the end |
|---|---|
| M0 | An image boots in qemu (x86_64 and aarch64) and CI boots it on every PR |
| M1 | Felix and felixd start on the image; a machine joins a cluster on first boot |
| M2 | Three VMs form a cluster, run a workload, lose one VM, and the workload continues. CI runs this. |
| M3 | Atomic A/B upgrades with rollback, and signed images |
| M4 | The scheduler, name service and `fx` shell run as programs on the log |
| M5 | Cloud images and Raspberry Pi |

## Open questions

- Linux or a kernel of our own?
  ([Discussion #22](https://github.com/GetFelix/ninelives/discussions/22))
- Our own PID 1, or systemd? ([#15](https://github.com/GetFelix/ninelives/issues/15))
- How images are built: mkosi is the proposal.
- How machines are upgraded: A/B root partitions with rollback are the proposal.
- How a machine proves its identity on first join. ([#16](https://github.com/GetFelix/ninelives/issues/16))
- Who decides a machine's role, and how a machine changes role later.
- Encrypting records and redacting history, since the log holds everything
  that passed through it.

## Licensing

This repository is MIT. If the kernel is Linux, a built image will contain it
and other GPL-licensed software. Publishing images means publishing the matching source,
or a written offer for it, for those components.
