# 0004: Felix and felixd ship from pinned, verified releases, not a submodule

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
3. where the upstream signs its checksums, verifies the signature on the
   checksum file against the identity and issuer in the manifest, then
   checks that the signed file lists the same SHA-256 for the artifact. The
   build fails if either check fails, so a manifest hash that the upstream
   never signed cannot ship,
4. writes an inventory of what it installed into the image.

The job that opens a bump pull request
([#52](https://github.com/GetFelix/ninelives/issues/52)) runs the same
signature check before anyone reviews it, so for a signed component the
reviewer is checking a hash the upstream vouched for, not one copied from a
web page. For a component with no signature (Felix 0.7.2) the hash is
trusted because a reviewer checked it when it was pinned, and nothing more.
The manifest says so for that component, with the issue that fixes it, and
CI refuses a change that drops, renames or removes a signed component.

The manifest changes only through a reviewed pull request, and the CI boot
and integration tests must pass on it.

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
| Build both from source in the image build | Reproducible in principle, but the image build would carry two more toolchains and break for reasons outside NineLives. felixd already publishes reproducible release builds, and Felix is to follow ([felix#2799](https://github.com/GetFelix/felix/issues/2799)). |
| Distribution packages | Neither project publishes them. |

## Consequences

- Felix's and felixd's place in an image can be read from one file, and
  every artifact is checked by hash, and against a signature where one
  exists.
- This record covers what NineLives takes from its own upstreams. The rest
  of the root (glibc, firmware, the tools the init uses) comes from the base distribution's signed package
  repositories, pinned to a snapshot ([0006](0006-debian-base.md)). The
  kernel is built from verified upstream source
  ([0007](0007-kernel-from-upstream-source.md)). The image's inventory
  lists every package with its version.
- NineLives waits for felixd and Felix releases. The nightly build against
  felixd's main branch covers most of the gap.
- This depends on both projects publishing Linux builds for every
  architecture NineLives targets, with signed checksums, built
  reproducibly. felixd does all three. Felix publishes its server for Linux
  x86_64 only and does not check that its server build is reproducible. On
  main, after v0.7.2, it signs the server `SHA256SUMS` with Sigstore
  ([felix#2845](https://github.com/GetFelix/felix/pull/2845)), so the first
  release that contains that can be checked like felixd. Only a tagged
  release is pinned: a nightly is not, and its regenerated `SHA256SUMS`
  drops the bundle
  ([felix#2797](https://github.com/GetFelix/felix/issues/2797),
  [felix#2796](https://github.com/GetFelix/felix/issues/2796),
  [felix#2799](https://github.com/GetFelix/felix/issues/2799)).

## Reopen when

- felixd stops publishing signed, reproducible release builds, or Felix
  does not start.
- NineLives needs a patched build of either one.
