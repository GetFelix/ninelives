# images

One directory per build target. `qemu-x86_64/` is the first and `qemu-aarch64/`
the second; cloud images and Raspberry Pi follow. How the image is built, checked, compared and
signed is in [the image build design](../docs/components/image.md).

## What builds today

CI's `image` job builds the x86_64 qemu `production` image from the commit,
checks it and uploads it as the `image-qemu-x86_64` artifact; `image-aarch64`
does the same for aarch64 on an arm64 runner (`image-qemu-aarch64`), and every
job below has an `-aarch64` twin. `kernel-rebuild`, `init-rebuild` and `image-rebuild` build the kernel, the init and the image again on other runners with the paths, CPU count, clock and user name changed (the scripts pin the umask and locale), and `image-compare` fails unless the image's `SHA256SUMS`, the kernel and the init are identical, with a diffoscope report if not. To run the same
build on a Linux host with docker or podman:

```sh
tools/image.sh --kernel <kernel-qemu-x86_64 artifact dir> --init <init binary> --out <dir>
```

For aarch64 set `NL_ARCH=aarch64`, on an arm64 host: the build is native, so the
host, the kernel and the init must all be aarch64.

Nothing built this way is signed or published. The `boot` job boots the image
in qemu; `tools/boot.sh --image <the .raw> --out <dir>` does the same on a
Linux host. Its qemu and UEFI firmware are the `boot` set in the architecture's
lock (`debian.lock`, `debian-aarch64.lock`).

## Pins

Everything the build uses is pinned here, and a change to any of it is a
reviewed pull request.

| File | Pins |
|---|---|
| [`tools.toml`](tools.toml) | mkosi and cosign (release and SHA-256), the SHA-256 of the Sigstore trusted root, the bootstrap container (digest), the Debian snapshot, the release of the root and of the tools, the tools' root packages |
| [`keys/sigstore/`](keys/sigstore/README.md) | the Sigstore trusted root the fetch verifies component signatures against, offline |
| [`debian.lock`](debian.lock), [`debian-aarch64.lock`](debian-aarch64.lock) | for x86_64 and aarch64, every Debian package of the root and of the tools by name, version and SHA-256, and each suite's `InRelease` hash. `scripts/image_lock.py generate` writes it; the build regenerates it and fails on a difference. |
| [`keys/debian/`](keys/debian/) | the Debian archive keys, with their fingerprints, checked against Debian's published ones |
| [`components.toml`](components.toml) | Felix and felixd ([decision 0004](../docs/decisions/0004-components-from-pinned-releases.md)) |

## Allowlists

| File | What the checks hold the image to |
|---|---|
| [`packages.toml`](packages.toml) | the packages in the root, exactly, each with its reason |
| [`users.toml`](users.toml) | `/etc/passwd` and `/etc/group`, exactly |
| [`executables.toml`](executables.toml) | every executable, ELF file and shared library; no shell or interpreter |
| [`symlinks.toml`](symlinks.toml) | links whose target is outside the image |
| [`budget.toml`](budget.toml) | size limits, from the first build plus 20% |

`qemu-ARCH/target.toml` holds the target's console, disk names, UUID seeds,
partition sizes and the names that differ by architecture (kernel image, EFI
fallback loader, root partition type, multiarch directory). `executables.toml`
and `rootfs/prune.toml` take `{triplet}` and `{ld_so}` from it. `scripts/check_components.py` and
`scripts/image_lock.py check` check the pin files' shape in CI.

## How components are checked

`scripts/fetch_image.py` downloads each artifact in `components.toml` and
checks its SHA-256 against the manifest. For a component with a Sigstore
signature it also downloads `SHA256SUMS` and its bundle and runs the pinned
cosign on them with the trusted root in `keys/sigstore/`. Nothing is fetched
to do this. The certificate's identity and issuer must equal the manifest's
exactly, and the artifact's hash must equal both the manifest's and the line
for its file name in the signed `SHA256SUMS`. Any mismatch stops the build. A
component with `kind = "none"` is checked by SHA-256 only, and its manifest
entry links the issue that tracks the missing signature. Both components
are signed today.
