# kernel

The pinned Linux long-term release and our config fragments. Stock upstream
Linux built from verified source, no patches, no loadable modules
([0001](../docs/decisions/0001-linux-kernel.md),
[0007](../docs/decisions/0007-kernel-from-upstream-source.md)). The design,
including every option and the checks CI runs, is in
[docs/components/kernel.md](../docs/components/kernel.md).

Kernels are built in CI on Linux, never locally. The `kernel` job in
[ci.yml](../.github/workflows/ci.yml) builds both architectures for the
qemu target and uploads `kernel-qemu-<arch>` (the kernel image, its
`.config` and `System.map`) for the image build
([#2](https://github.com/GetFelix/ninelives/issues/2)). Nothing inside the
artifact proves where it came from, so the image build must take only the
artifact of its own workflow run, or one from a push to `main`, and never
the latest artifact with that name: any pull request's run can upload one.

## Files

- `VERSION`: the exact release (6.18.64), its tarball and signature URLs,
  and the tarball's SHA-256. TOML.
- `keys/`: `fingerprints` lists the primary keys allowed to sign the
  tarball; the `.asc` files are those keys. Only Greg Kroah-Hartman's key
  is listed, because it signed every 6.18.y release so far.
- `fragments/`: applied after upstream `allnoconfig` and `hardening.config`,
  in this order: `common.config`, `<arch>.config`, `<target>.config`,
  `<target>-<arch>.config`. Every option line sits under a
  `# <who>: <why>` comment.
- `resolved/`: the full resolved config per target and architecture. CI
  regenerates it and fails on any byte of difference.
- `policy.toml`: options that must be set or unset, upstream
  `hardening.config` lines that cannot apply and why, the expected
  sysctls, the command line words the kernel policy requires, and the
  image parameters the command line may carry besides them.
- `section-mismatches/<arch>.txt`: the modpost section mismatches a build
  may report. x86_64 builds with `SECTION_MISMATCH_WARN_ONLY` because 6.18's
  FineIBT code trips modpost when modules are off, so `kernel_build.sh`
  fails on any mismatch not listed, and on a listed one that went away.
- `hardening-exceptions.toml`: each kernel-hardening-checker finding that
  is not fixed, with the reason (S2).

## What CI does

On a pull request that touches this directory, the kernel scripts or
`ci.yml` (and on every push to `main`), for each architecture, in a Debian
13 container pinned by digest with its toolchain from one
snapshot.debian.org timestamp:

1. `scripts/fetch_kernel.py` downloads the tarball, checks its SHA-256
   against `VERSION`, then checks the `.tar.sign` signature over the
   decompressed tarball with `gpgv` against `keys/`. Either failing stops
   the job before anything is unpacked.
2. `scripts/kernel_build.sh resolve` runs `merge_config.sh -n` (upstream
   `allnoconfig` with the fragments forced on top) and `olddefconfig`,
   with `LLVM=1`.
3. The resolved config is compared with `resolved/`, then
   `scripts/check_kernel_config.py config` checks that every fragment and
   upstream hardening line landed as written, that every option it names
   exists in the tree, and that `policy.toml` holds.
4. kernel-hardening-checker, pinned by commit, runs on the config, the
   policy's command line words and sysctls. Every finding is fixed or in
   `hardening-exceptions.toml`.
5. The kernel is built and uploaded.

The unit tests (`python3 -I -m unittest discover -s scripts/tests`) and a
lint of the fragments and `policy.toml` run on every pull request, as does
`fetch_kernel.py --check --base`, which compares the pin with the target
branch's: the version may not go down or leave the 6.18.y series, and
`keys/` may not change in a pull request that changes `VERSION`.

## Bumping the version

Every stable release of the pinned line is taken (kernel.md, "Point
releases").

1. Find the release: the `longterm` entry for 6.18 in
   <https://www.kernel.org/releases.json>.
2. Pin it. This downloads the tarball and signature, verifies the signature
   against `keys/`, and only then writes the new version, URLs and hash
   into `VERSION`:

   ```sh
   python3 -I scripts/fetch_kernel.py --out /path/to/scratch --bump 6.18.65
   ```

   Check the hash it wrote against the `linux-6.18.65.tar.xz` line of
   <https://cdn.kernel.org/pub/linux/kernel/v6.x/sha256sums.asc>. That file
   only shows kernel.org's mirrors agree, not who made the release; the
   signature is the proof.
3. Open the pull request. If the release changed the resolved config, CI's
   check 1 fails and the job uploads the new one as
   `kernel-config-qemu-<arch>`. Review the diff line by line, copy it into
   `resolved/qemu-<arch>.config`, and push. A new option that weakens the
   policy is a fragment change with a reason, never a quiet update of
   `resolved/`.
4. A new `MITIGATION_*` option on x86_64 fails check 3 until it is added
   to `[x86_64.set]` in `policy.toml` and to `fragments/x86_64.config`, or
   to `[x86_64.not_applicable]` with the reason it cannot apply.

If a release is signed by a key not in `keys/fingerprints`, the bump
fails. Adding a key is its own pull request (CI refuses one that also
changes `VERSION`): the fingerprint has to match
the one kernel.org publishes at <https://www.kernel.org/signature.html>,
and the key comes from kernel.org's
[pgpkeys](https://git.kernel.org/pub/scm/docs/kernel/pgpkeys.git) or its
WKD, compared with each other.

Moving to the next long-term line follows kernel.md's rule and is one pull
request with nothing else in it, including the change to `SERIES` in
`scripts/fetch_kernel.py`. `.github/CODEOWNERS` names an owner for
`VERSION` and `keys/`; with one maintainer that is an audit marker, not a
second reviewer.

## The GPL and published images

The kernel is GPL-2.0-only, and an image carries a binary of it. Whoever
publishes an image has to make the complete corresponding source of that
exact kernel available to everyone who receives the image, under the same
licence (GPL-2.0 section 3). For this kernel that source is:

- the upstream tarball named in `VERSION`, unmodified;
- the resolved `.config` it was built with, which is the `.config` in the
  `kernel-qemu-<arch>` artifact and equals `resolved/`;
- what controls the build: `scripts/kernel_build.sh`, the fragments and the
  environment it sets (`LLVM=1`, the fixed build user, host and timestamp).

The image must also carry the licence text (GPL-2.0 sections 1 and 3):
the tarball's `COPYING` and `LICENSES/preferred/GPL-2.0`, installed in the
image.

The image design plans to meet this from the first published image (S12)
by publishing the tarball, the config and these scripts next to each
image, from the same place, and by shipping the licence text in the image. A link to kernel.org alone is not enough,
because kernel.org may drop the release. Firmware built into the kernel
with `CONFIG_EXTRA_FIRMWARE` (the x86_64 microcode, once the image build
adds it) is not GPL code: it is redistributed under its own licence, which
has to allow that, and is published with the rest.

## Not done yet

- Microcode is not built in yet: `CONFIG_EXTRA_FIRMWARE` needs Debian's
  microcode packages from the image build's lock, and CI check 6 (AMD
  patch hashes) comes with it.
- CI check 5 (the UKI's command line) needs the UKI;
  `check_kernel_config.py cmdline` is the check it will run.
- Comparing the tarball with `git archive` of the signed stable tag, and
  opening a pull request for each release, belong to the bump job
  ([#57](https://github.com/GetFelix/ninelives/issues/57)).
