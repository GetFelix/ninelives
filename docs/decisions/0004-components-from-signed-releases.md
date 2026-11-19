# 0004: Felix and felixd ship from pinned releases, not a submodule

**Status:** Accepted, 2026-11-18.

## Context

The image ships two programs NineLives does not build itself: Felix (the
control plane and broker) and felixd. felixd was pinned as a git submodule.
By the time this record was written the submodule pointed at a commit 123
commits behind felixd's main branch and older than felixd v0.1.0: a version
nobody released or tested as a whole, and nothing in this repository said
which felixd an image would contain.

An operating system has to answer "what is in this image and where did it
come from?" for every binary in it.

## Decision

Every component NineLives ships but does not build is listed in
[`images/components.toml`](../../images/components.toml): its version, the
release it comes from, and the URL and SHA-256 of each artifact per
architecture. The image build:

1. downloads each artifact from the URL in the manifest,
2. checks it against the SHA-256 in the manifest, and fails the build on a
   mismatch,
3. where the upstream signs its checksums, verifies that signature against
   the identity and issuer in the manifest, and fails the build if it does
   not verify,
4. writes an inventory of what it installed into the image.

The manifest changes only through a reviewed pull request
([#52](https://github.com/GetFelix/ninelives/issues/52) opens one on each
upstream release), and the CI boot
and integration tests must pass on it. A component with no upstream
signature says so in the manifest, with the issue that tracks adding one.

A workflow input and a nightly job build felixd from its main branch and run
NineLives' tests against it, so a break shows up before felixd releases
([#51](https://github.com/GetFelix/ninelives/issues/51)). Those builds are
never published as NineLives images.

What NineLives relies on from felixd is written down in
[felixd-contract.md](../felixd-contract.md), so a change to that boundary is
visible in review.

## Options considered

| Option | Why not |
|---|---|
| Keep the submodule | Pins an arbitrary commit, drifts silently, and makes the image build compile felixd and its toolchain. Felix and felixd would be handled differently for no reason. |
| Build both from source in the image build | Reproducible in principle, but the image build would carry two more toolchains and break for reasons outside NineLives. Both projects already publish reproducible release builds. |
| Distribution packages | Neither project publishes them. |

## Consequences

- An image's contents can be read from one file, and every artifact is
  checked by hash, and by signature where one exists.
- NineLives waits for felixd and Felix releases. The nightly build against
  felixd's main branch covers most of the gap.
- This depends on both projects publishing Linux builds for every
  architecture NineLives targets, with signed checksums. felixd does. Felix
  publishes its server for Linux x86_64 only, and its checksums are not
  signed yet ([felix#2797](https://github.com/GetFelix/felix/issues/2797),
  [felix#2796](https://github.com/GetFelix/felix/issues/2796)).

## Reopen when

- Either upstream stops publishing reproducible release builds.
- NineLives needs a patched build of either one.
