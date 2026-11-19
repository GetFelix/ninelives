# 0003: Images are built with mkosi, in CI

**Status:** Accepted, 2026-11-18. Holds through M2
([#1](https://github.com/GetFelix/ninelives/issues/1),
[#2](https://github.com/GetFelix/ninelives/issues/2)).

## Context

M0's first image has to come from something, and every later milestone
builds on it: M1 adds a data partition, M3 adds A/B root partitions and
signing, M5 adds clouds and a Raspberry Pi.

Images only build on Linux, and development machines are often Macs.

## Decision

Images are built with [mkosi](https://github.com/systemd/mkosi), pinned to
an exact release, from configuration in `rootfs/` and `images/`. They are
built in CI on Linux runners. A local run boots an image that CI built.

The image does not contain systemd as its init
([0002](0002-our-own-init.md)). The kernel command line names our init.

Two choices are made inside this one, each in its own issue: the base
distribution the root is built from (#1), and whether the kernel comes from
that distribution or is built from source (#3).

Every image build is reproducible. CI builds each image twice from the same
commit and fails if the two differ.

## Options considered

| Option | Why not |
|---|---|
| bootc (OCI images as the OS) | Puts a container registry on the upgrade path, and is built around Fedora, CentOS and systemd. |
| Buildroot or Yocto | Very small images and any init, but a build system to learn and keep up, and long builds. Worth looking at again for the Raspberry Pi in M5. |
| Our own script (debootstrap or similar, plus systemd-repart) | No new tool, but all the partitioning, unified kernel image and A/B layout logic would be ours to maintain. |

## Consequences

- One config describes the disk layout, which M3's A/B partitions build on.
- mkosi assumes systemd in places. Where a default expects systemd as PID 1
  we override it, and each override is commented in the config.
- Nobody builds images on a Mac. CI artifacts are the images people boot.

## Reopen when

- mkosi's systemd assumptions cost more to override than a plain script
  would cost to maintain.
- M5, if a Raspberry Pi image is easier with Buildroot or Yocto.
