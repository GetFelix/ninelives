# images

One directory per build target: qemu x86_64 and aarch64 first, then cloud
images and Raspberry Pi. How the image is built, checked, compared and
signed is in [the image build design](../docs/components/image.md). None of
it is built yet; the pin and allowlist files that design names
(`debian.lock`, `packages.toml`, `users.toml`, `executables.toml`,
`tools.toml`, `budget.toml`) arrive with the build
([#1](https://github.com/GetFelix/ninelives/issues/1)).

[`components.toml`](components.toml) lists every program the image ships but
does not build (Felix and felixd), pinned to a release with a sha256 per
architecture and the upstream signature to verify
([decision 0004](../docs/decisions/0004-components-from-pinned-releases.md)).
Change it only in a reviewed pull request. `scripts/check_components.py`
checks its shape in CI.
