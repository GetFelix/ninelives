# Contributing to NineLives

NineLives is an operating system. Every machine in a cluster runs it, and the
promise it makes (a workload survives losing a machine, with nothing lost or
repeated) has to hold under scrutiny. These rules exist so it does.

## How work moves

1. **Decisions first.** A choice that is hard to undo or that constrains
   later work gets a record in [`docs/decisions/`](docs/decisions/) before
   code depends on it. The README there says when one is needed and what it
   contains.
2. **Design before code.** Every component gets a design document in
   [`docs/components/`](docs/components/) before its first code lands. The
   design and the code go in separate pull requests, and the design is
   reviewed first.
3. **Small pull requests, one concern each.** A pull request that changes
   behaviour updates the docs, the tests and the component's README in the
   same change.
4. **Independent review.** Every pull request is reviewed by someone who did
   not write it, and the review tries to break it: the reviewer looks for
   the input, timing or failure that makes it wrong, not just for style.
   Designs and decision records get the same review.
5. **CI decides.** `main` requires the `CI ok` check, which passes only when
   every job does. Pull requests are squash-merged.

## Component designs

A design in `docs/components/<name>.md` has these sections, in this order:

- **What it does.** One paragraph.
- **What it does not do.** The non-goals. This is where scope creep gets
  stopped, so be specific.
- **Interfaces.** What it reads and writes: files, system calls, signals,
  records, other processes. For each, the exact format or a link to it.
- **Invariants.** Statements that are always true, written so a test can
  check each one. "No orphaned process outlives one reap cycle" is an
  invariant. "Handles processes correctly" is not.
- **Failure modes.** For each way it can fail (a dependency crashes, hangs,
  floods output, the disk fills, power is cut, the component itself
  panics), what happens and why that is acceptable.
- **Security.** What it runs as, what it can reach, what it trusts and why,
  which [requirements](docs/requirements.md) it meets, and which threats in
  the [threat model](docs/threat-model.md) it answers.
- **Test plan.** Which test proves which invariant, and which failure mode
  is exercised where (unit, qemu, fault injection).
- **Open questions.** Anything not decided, with who decides and by when.

## Code

- System code is Rust, stable, edition 2024
  ([0005](docs/decisions/0005-rust-and-unsafe.md)). Every crate sets
  `#![deny(unsafe_code)]`. The only exception is one `#[allow(unsafe_code)]
  mod sys` per crate, every `unsafe` block there has a `// SAFETY:`
  comment, and CI enforces both.
- `cargo fmt`, `cargo clippy -- -D warnings` and `cargo deny` pass.
- Errors are returned and handled. A program that runs as PID 1 or as a
  system service does not panic on input it can survive.
- Each crate has a README: what it is, where it runs, what it provides, how
  to build and test it.
- Comments say why, briefly: an ordering requirement, a failure mode, a
  limit that exists for a reason. They do not restate the code.
- Components the image ships but does not build are listed in
  [`images/components.toml`](images/components.toml)
  ([0004](docs/decisions/0004-components-from-pinned-releases.md)). Never
  vendor or submodule them.

## Tests

- A test exists for each invariant in the component's design, and its name
  or comment says which.
- A fix comes with a test that fails without it. Revert the fix, watch the
  test fail, restore it.
- Anything that boots is tested by booting it: CI builds the image and boots
  it in qemu on every pull request.
- Fault injection (power loss during shutdown, a full disk, a service that
  floods its output) runs on a schedule. A fault test passes when the system
  does what the design says, not merely when nothing crashed.

## Images

Images build on Linux only, in CI
([0003](docs/decisions/0003-mkosi-image-build.md)). On a Mac, boot the image
CI built. Image builds are reproducible: CI builds twice and compares.

## Writing docs

Plain and short. Say what is true now. If something is not built, say so;
if a claim cannot be backed by code, fix the claim. Decided things are
written as decided, open things as open, and the two are never blurred.
