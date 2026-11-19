# init

Our init, PID 1, in Rust ([decision 0002](../docs/decisions/0002-our-own-init.md)):
mounts, reaping, starting and restarting the role's services in their own
cgroups, and an ordered shutdown that ends in sync and unmount.

No code yet. The design comes first ([#46](https://github.com/GetFelix/ninelives/issues/46))
and will live in [docs/components/init.md](../docs/components/).
