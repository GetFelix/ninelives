# Decisions

Each file here records one choice about how NineLives is built: what we
chose, what else we looked at and why we turned it down, what it costs, and
what would make us reopen it. The design ([design.md](../design.md)) says what
the system does. These files say why it is built the way it is.

| # | Decision | Status | Holds through |
|---|---|---|---|
| [0001](0001-linux-kernel.md) | The kernel is stock Linux | Accepted | M2, then reviewed |
| [0002](0002-our-own-init.md) | PID 1 is our own init, written in Rust | Accepted | M2, then reviewed at M4 |
| [0003](0003-mkosi-image-build.md) | Images are built with mkosi, in CI | Accepted | M2 |
| [0004](0004-components-from-pinned-releases.md) | Felix and felixd ship from pinned, verified releases, not a submodule | Accepted | Open-ended |
| [0005](0005-rust-and-unsafe.md) | System code is Rust, and `unsafe` lives in one place | Accepted | Open-ended |
| [0006](0006-debian-base.md) | The root is built from a minimal, pinned Debian 13 | Accepted | M2 |
| [0007](0007-kernel-from-upstream-source.md) | The kernel is built from upstream source with our own config | Accepted | Open-ended |
| [0008](0008-ca-keys-and-machine-certificates.md) | CA keys live offline and on issuer machines; machines renew their own certificates | Accepted | M1 |
| [0009](0009-node-tokens-from-felix-exchange.md) | Node tokens come from Felix's token exchange, with the issuer as identity provider | Proposed | M1 |

Decisions are measured against the
[security and networking requirements](../requirements.md).

## When to write one

Write a record when a choice is hard to undo, constrains later work, or is
one a reviewer will reasonably ask about. Picking a library inside one
component usually is not; picking the kernel, the init, the image format or
a wire format is.

## Format

Copy an existing record. Every record has these sections:

- **Status:** Proposed, Accepted, or Superseded by a later number. A record
  is Proposed while it is open for argument, and code may not depend on it
  yet. It becomes Accepted when it merges after review. A record
  is never edited to say something else once accepted. A new record
  supersedes it, and the old one gets a line pointing forward.
- **Context:** what forces the choice now, and which milestone needs it.
- **Decision:** one paragraph, specific enough to check code against.
- **Options considered:** each alternative with the real reason it lost.
- **Consequences:** what this commits us to, including the costs.
- **Reopen when:** the conditions that would make us decide again. A record
  without them is a record nobody will revisit.

A record is merged through a pull request like code, and gets an independent
review before it is accepted.
