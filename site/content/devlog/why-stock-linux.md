+++
title = "Why NineLives runs on stock Linux"
description = "A Rust kernel is tempting. I'm starting on Linux anyway, and keeping the door open."
date = 2026-10-28
slug = "why-stock-linux"
+++

The first question anyone asks is why I'm not writing a kernel. There are good Rust crates now, and booting, paging and a basic scheduler are weeks of work, not years. I opened [a discussion](https://github.com/GetFelix/ninelives/discussions/22) to write the answer down, and here is the short version.

What is new in NineLives sits above the kernel: workloads that survive losing a machine, atomic upgrades, system programs that keep their state in the log. None of that needs a new kernel, and all of it is plenty of work. The first thing I want to see is three VMs, one killed, and nothing lost.

The hard part of a kernel is what comes after boot.

- **Drivers.** NVMe, real network cards, whatever each cloud exposes. Most of Linux is drivers.
- **Storage.** Felix's durability depends on `fsync`, `pread` and preallocation behaving exactly as documented. A new kernel would have to earn that trust first.
- **Networking.** Felix runs on tokio and QUIC over UDP. A scratch kernel needs a network stack and a port of that runtime.
- **Security and many cores.** Every fix would be mine to write.

I did look at what is out there. [Asterinas](https://github.com/asterinas/asterinas) aims to be compatible with Linux at the syscall level, so felixd could run on it unchanged. That makes it an experiment, not a rewrite. [Hermit](https://github.com/hermit-os/kernel) is a unikernel, and a node that only runs Felix and felixd could be a single image. [Redox](https://gitlab.redox-os.org/redox-os/redox) and [Theseus](https://github.com/theseus-os/Theseus) are worth studying for their ideas on recovery. [Rust for Linux](https://rust-for-linux.com/) is the middle path if I ever need something inside the kernel.

A kernel of our own could give a smaller trusted base, deterministic scheduling that makes replay stronger, and syscalls designed around the log. It would also cost years before I got back to the parts nobody else has.

So the plan is stock Linux through the third milestone, with felixd leaning on as little of the kernel as it can. After that I'll try running felixd on Asterinas and see what breaks. If you think I have the trade-off wrong, or I missed a project, [reply in the discussion](https://github.com/GetFelix/ninelives/discussions/22).
