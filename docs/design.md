# NineLives design

## Goal

A cluster that behaves like one machine, where the system's memory is a
replicated log. Any program's state can be rebuilt from the log, so failover,
migration and time travel are the same operation.

[felixd's design](https://github.com/GetFelix/felixd/blob/main/docs/design.md)
covers how workloads run, hold leases, write exactly-once output and replay.
This document covers the operating system around it: what boots on each
machine, how a machine joins, and which system services live on the log.

## What runs where

Every machine runs the NineLives image: Linux, early init and felixd. Three or
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
| Upgrades | Machines: A/B image swap. Workloads: run the new module over recorded inputs, compare, switch at an offset. |

## The kernel

Stock Linux, pinned to a release and configured with fragments in `kernel/`.
Drivers, filesystems and networking are not where NineLives is different, and
a fork would cost far more than it returns. A custom kernel can be a research
track later; nothing above the kernel should depend on that happening.

## The image

- Built with mkosi from `rootfs/` and `images/<target>/`.
- systemd-boot, unified kernel images, and secure boot once signing exists.
- A read-only root and two root partitions. An upgrade writes the inactive
  one and switches on reboot; a failed boot falls back.
- Machine state that must survive an upgrade (Felix data, the machine's
  identity and credentials) lives on a separate data partition.

## Boot sequence

1. Firmware loads systemd-boot, which loads the unified kernel image.
2. Early init mounts the root and data partitions and brings up the network.
3. On first boot, the machine joins a cluster: it reads a join token from
   cloud metadata, a config partition or the kernel command line, registers,
   and stores its credentials on the data partition.
4. The control plane starts on core machines and brokers on core and storage
   machines, as systemd services ([topology.md](topology.md)).
5. felixd starts, connects to Felix and claims the work the log assigns it.

The first milestones use systemd as PID 1. Whether NineLives needs its own
init is an open question; it depends on how much of systemd's job moves onto
the log.

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

- Our own PID 1, or systemd?
- How a machine proves its identity on first join.
- Who decides a machine's role, and how a machine changes role later.
- Encrypting records and redacting history, since the log holds everything
  that passed through it.

## Licensing

This repository is MIT. A built image contains the Linux kernel and other
GPL-licensed software. Publishing images means publishing the matching source,
or a written offer for it, for those components.
