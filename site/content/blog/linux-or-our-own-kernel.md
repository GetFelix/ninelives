+++
title = "Linux or our own kernel?"
description = "The first open question, with the case for each side. Nothing is chosen yet."
date = 2026-10-28
slug = "linux-or-our-own-kernel"
aliases = ["/devlog/why-stock-linux/", "/devlog/linux-or-our-own-kernel/"]

[extra]
card = "/img/blog/linux-or-our-own-kernel/1-three-vms.svg"

[taxonomies]
tags = ["kernel", "design", "linux"]
+++

The first question anyone asks is whether NineLives will have its own kernel. There are good Rust crates now, and booting, paging and a basic scheduler are weeks of work, not years. It isn't decided. I opened [a discussion](https://github.com/GetFelix/ninelives/discussions/22) to work it out, and this is the short version of both sides.

## The case for stock Linux

What is new in NineLives sits above the kernel: workloads that survive losing a machine, atomic upgrades, system programs that keep their state in the log. None of that obviously needs a new kernel, and all of it is plenty of work. The first thing I want to see is three VMs, one killed, and nothing lost.

{{ <figure src="/img/blog/linux-or-our-own-kernel/1-three-vms.svg" alt="Three machines, each running Linux, sit over one log of records. The middle machine is faded and dashed because it died. The cat steps along a curved arrow from it onto the third machine. Every record in the log is still there, and the newest is amber." caption="The first goal on stock Linux: kill one of three VMs and the workload moves to another with every record intact." /> }}

The hard part of a kernel is what comes after boot.

- **Drivers.** NVMe, real network cards, whatever each cloud exposes. Most of Linux is drivers.
- **Storage.** Felix's durability depends on `fsync`, `pread` and preallocation behaving exactly as documented. A new kernel would have to earn that trust first.
- **Networking.** Felix runs on tokio and QUIC over UDP. A scratch kernel needs a network stack and a port of that runtime.
- **Security and many cores.** Every fix would be ours to write.

## The case for a kernel of our own

A kernel built for NineLives could give a smaller trusted base, deterministic scheduling that makes replay stronger, and syscalls designed around the log. With today's crates, booting, paging and a basic scheduler are weeks of work. And nobody else is building an OS with a replicated log at the center, so there's a real research story. The cost is everything after boot, listed above.

{{ <figure src="/img/blog/linux-or-our-own-kernel/2-two-stacks.svg" alt="Two stacks side by side, each with the cat on top of a felix and felixd layer, over a kernel, over hardware. In the stock Linux stack the kernel has six filled parts: boot, paging, scheduler, drivers, storage and network. In the own kernel stack boot, paging and scheduler are filled, while drivers, storage and network are empty amber outlines." caption="Both stacks run the same felix and felixd. Our own kernel gets boot, paging and a scheduler in weeks; drivers, storage and networking are what it would still have to earn." /> }}

There are middle paths. [Asterinas](https://github.com/asterinas/asterinas) aims to be compatible with Linux at the syscall level, so felixd could run on it unchanged, which makes it an experiment rather than a rewrite. [Hermit](https://github.com/hermit-os/kernel) is a unikernel, and a node that only runs Felix and felixd could be a single image. [Redox](https://gitlab.redox-os.org/redox-os/redox) and [Theseus](https://github.com/theseus-os/Theseus) are worth studying for their ideas on recovery. [Rust for Linux](https://rust-for-linux.com/) is the route if something ever has to live inside Linux.

{{ <figure src="/img/blog/linux-or-our-own-kernel/3-middle-paths.svg" alt="A line of five stops from stock Linux on the left to our own kernel on the right. They are stock Linux, Rust for Linux, Asterinas with Linux syscalls, Hermit as a unikernel, and our own kernel. The cat sits on stock Linux with amber eyes, and a curved arrow with a question mark arcs from it to the far end. Below, an arrow runs from reuse more to write more." caption="The middle paths reuse more of what exists. The open question is whether starting on Linux makes moving along this line harder later." /> }}

## What I don't know yet

- Is the first goal a cluster that survives losing a machine, or a kernel designed around the log? The answer probably decides this.
- Would starting on Linux make it harder to move later, or can felixd lean on so little of the kernel that it doesn't matter?
- Is Asterinas close enough to Linux that trying it is an experiment rather than a rewrite?
- Are single-purpose nodes enough, or does every machine need a general-purpose OS?
- What would we give up in debugging, tooling and hardware support?

If you have a view on any of these, or know a project I missed, [reply in the discussion](https://github.com/GetFelix/ninelives/discussions/22).
