# rootfs

The mkosi definition of the root ([decision 0003](../docs/decisions/0003-mkosi-image-build.md)):
a minimal Debian 13 pinned to a snapshot
([0006](../docs/decisions/0006-debian-base.md)).

`mkosi.conf` is what every target shares; `images/<target>/mkosi.conf`
includes it. mkosi only installs the packages and runs Debian's maintainer
scripts, offline, from the verified store. `scripts/image_stage.py` then
removes the packages the image does not need, unpacks Felix and felixd as
`components.toml` here says, adds the init, `/etc/passwd`, `/etc/group` and
`os-release`, and normalises the tree. The root is sealed as erofs and
dm-verity by `scripts/image_assemble.py`; see
[the image design](../docs/components/image.md#building-the-root).

[`components.toml`](components.toml) lists which members of each component's
archive are installed, and where.
