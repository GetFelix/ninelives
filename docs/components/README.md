# Component designs

One document per component, written and reviewed before the component's
code. The required sections are in
[CONTRIBUTING.md](../../CONTRIBUTING.md#component-designs).

| Component | Design | Status |
|---|---|---|
| init (PID 1) | [init.md](init.md) ([#46](https://github.com/GetFelix/ninelives/issues/46)) | First slice in [`init/`](../../init/) ([#47](https://github.com/GetFelix/ninelives/issues/47)); services and shutdown next ([#48](https://github.com/GetFelix/ninelives/issues/48)) |
| kernel configuration | [kernel.md](kernel.md) ([#3](https://github.com/GetFelix/ninelives/issues/3)) | In review ([0001](../decisions/0001-linux-kernel.md), [0007](../decisions/0007-kernel-from-upstream-source.md)) |
| Image build | [image.md](image.md) | Design in review; not built ([#1](https://github.com/GetFelix/ninelives/issues/1)) |
| First-boot join and machine credentials | [join.md](join.md) ([#5](https://github.com/GetFelix/ninelives/issues/5), [#63](https://github.com/GetFelix/ninelives/issues/63), [#64](https://github.com/GetFelix/ninelives/issues/64), [#73](https://github.com/GetFelix/ninelives/issues/73)) | Design in review ([0008](../decisions/0008-ca-keys-and-machine-certificates.md), [0009](../decisions/0009-node-tokens-from-felix-exchange.md) proposed); not built |
