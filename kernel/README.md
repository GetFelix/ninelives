# kernel

The pinned Linux long-term release and our config fragments. Stock upstream
Linux built from verified source, no patches, no loadable modules
([0001](../docs/decisions/0001-linux-kernel.md),
[0007](../docs/decisions/0007-kernel-from-upstream-source.md)). The design,
including the pinned version (6.18), every option and the checks CI runs, is
in [docs/components/kernel.md](../docs/components/kernel.md).

Nothing is built yet ([#3](https://github.com/GetFelix/ninelives/issues/3)).
When it is, this directory holds:

- `VERSION`: the exact release and the tarball's SHA-256.
- `keys/`: the stable maintainers' key fingerprints the build checks the
  tarball's signature against.
- `fragments/`: config fragments applied to upstream `allnoconfig` and
  `hardening.config`. Every line says which requirement, component or
  target needs it.
- `resolved/`: the full resolved config per target and architecture. CI
  regenerates it and fails on any difference.
- `cmdline/`: the exact kernel command line per target.
- `policy.toml`: options that must be set or unset, expected sysctls and
  forbidden command line words, read by CI and by the qemu tests.
- `hardening-exceptions.toml`: each kernel-hardening-checker finding that
  is not fixed, with the reason (requirement S2).

Kernels are built in CI on Linux, never locally.
