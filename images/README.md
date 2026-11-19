# images

One directory per build target. `qemu-x86_64/` is the first; aarch64, cloud
images and Raspberry Pi follow. How the image is built, checked, compared and
signed is in [the image build design](../docs/components/image.md).

## What builds today

CI's `image` job builds the x86_64 qemu `production` image from the commit,
checks it and uploads it as the `image-qemu-x86_64` artifact. `kernel-rebuild`, `init-rebuild` and `image-rebuild` build the kernel, the init and the image again on other runners with the paths, CPU count, clock and user name changed (the scripts pin the umask and locale), and `image-compare` fails unless the image's `SHA256SUMS`, the kernel and the init are identical, with a diffoscope report if not. To run the same
build on a Linux host with docker or podman:

```sh
tools/image.sh --kernel <kernel-qemu-x86_64 artifact dir> --init <init binary> --out <dir>
```

Nothing built this way is signed or published. The `boot` job boots the image
in qemu; `tools/boot.sh --image <the .raw> --out <dir>` does the same on a
Linux host. Its qemu and UEFI firmware are the `boot` set in `debian.lock`.

## Pins

Everything the build uses is pinned here, and a change to any of it is a
reviewed pull request.

| File | Pins |
|---|---|
| [`tools.toml`](tools.toml) | mkosi (release and SHA-256), the bootstrap container (digest), the Debian snapshot, the release of the root and of the tools, the tools' root packages |
| [`debian.lock`](debian.lock) | every Debian package of the root and of the tools by name, version and SHA-256, and each suite's `InRelease` hash. `scripts/image_lock.py generate` writes it; the build regenerates it and fails on a difference. |
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

`qemu-x86_64/target.toml` holds the target's console, disk names, UUID seeds
and partition sizes. `scripts/check_components.py` and
`scripts/image_lock.py check` check the pin files' shape in CI.
