# 0005: System code is Rust, and `unsafe` lives in one place

**Status:** Accepted, 2026-11-18.

## Context

NineLives is about to write its first program that runs on every machine:
the init ([0002](0002-our-own-init.md)). If PID 1 crashes, the kernel
panics. Felix and felixd are written in Rust, and the people working on
NineLives work in it.

## Decision

Programs that run on a NineLives machine are written in Rust, on the stable
toolchain, edition 2024. Build scripts and site tooling may stay in Python
or shell.

Every crate sets `#![deny(unsafe_code)]`. A crate that has to wrap system
calls the safe libraries do not cover does it in one module, declared as
`#[allow(unsafe_code)] mod sys;`, and nowhere else. (`forbid` would be
stricter, but it cannot be relaxed for one module, so `deny` plus a single
`allow` is the rule.) In that module:

- each `unsafe` block has a `// SAFETY:` comment that says why it is sound,
  and review checks it;
- the safe wrapper is the only thing the rest of the crate may call;
- adding an `unsafe` block needs a reviewer who did not write it.

System calls go through [rustix](https://github.com/bytecodealliance/rustix)
where it covers them, which keeps most crates free of `unsafe` entirely.

Dependencies are few and pinned in `Cargo.lock`. `cargo deny` checks
licenses, advisories and duplicate versions in CI. A new dependency in a
program that runs on the machine needs a sentence in its pull request saying
why it is worth carrying.

Panics are not error handling. Code that runs as PID 1 or in a system
service returns errors and handles them; `unwrap` and `expect` are allowed
only where the invariant is local and stated in the message.

## Options considered

| Option | Why not |
|---|---|
| C | No memory safety, and it would be the only C in the stack. |
| Go | A runtime and garbage collector in PID 1, and a second language next to Felix and felixd. |
| Rust with `unsafe` wherever needed | Spreads the code a reviewer must check line by line across the whole tree. |

## Consequences

- The `unsafe` surface is small and easy to find:
  `grep -rn 'allow(unsafe_code)'` lists every place it is allowed, and a
  second one in a crate fails review.
- Some system calls need a hand-written wrapper in `sys` until rustix covers
  them.
- The same toolchain and lint setup as felixd and Felix, so a contributor
  from either project is at home here.

## Reopen when

- A component cannot be written without `unsafe` outside a `sys` module,
  in which case the design for that component says why.
