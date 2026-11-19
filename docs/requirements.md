# Security and networking requirements

NineLives has to be very secure and fast on the network. These are the
requirements that make those two words checkable. Every decision record,
component design and kernel config change is measured against them, and
each one says how it is checked and from which milestone.

A requirement is changed through a pull request that says why, like a
decision record.

## Security

The full threat model (who the attacker is, what they can reach, what we
protect) is written before M1
([#56](https://github.com/GetFelix/ninelives/issues/56)). These hold
whatever it finds.

| # | Requirement | Checked by | From |
|---|---|---|---|
| S1 | **Minimal surface.** A production image contains only what a listed requirement or component needs: no package manager, compiler, shell, SSH server or debug tools. A development image may add a serial console shell and says so in its name. | The image inventory is diffed against an allowlist in CI | M0 |
| S2 | **No loadable kernel modules**, and kernel hardening per [0007](decisions/0007-kernel-from-upstream-source.md). | A CI check on the built kernel config | M0 |
| S3 | **Read-only root.** Nothing on the root changes at run time. State lives only on the data partition. | qemu test: writes to the root fail | M0 |
| S4 | **Every shipped binary is pinned and verified**: by hash and upstream signature for components ([0004](decisions/0004-components-from-pinned-releases.md)), from a pinned snapshot for packages ([0006](decisions/0006-debian-base.md)), and from a verified tarball for the kernel. | The image build fails closed | M0 |
| S5 | **Reproducible images.** The same commit builds the same bytes. | CI builds twice and compares | M0 |
| S6 | **Each service is isolated**: its own user, its own cgroup, no ambient capabilities, a seccomp filter, Landlock limits on the files it may touch. | The init's tests, per service | M1 |
| S7 | **Only Felix's ports are open**, on the interfaces the role needs. Everything else is dropped by default. | qemu test scans the machine from outside | M1 |
| S8 | **Secrets never go on the log** and never on the root. Credentials live on the data partition, readable only by the service that needs them. | Design review, and a test that greps the log and root | M1 |
| S9 | **Patch deadline.** A fixed image is built within 72 hours of a security fix marked critical, for any package, component or kernel in the image, and within 7 days for others. | Advisory feeds open pull requests automatically ([#57](https://github.com/GetFelix/ninelives/issues/57)); the time to merge is tracked | M1 |
| S10 | **Verified boot.** The firmware checks a signed kernel image, and the kernel checks the root against a dm-verity hash before using it. | qemu test with secure boot on: a modified root does not boot | M3 ([#9](https://github.com/GetFelix/ninelives/issues/9)) |

## Networking

Felix's traffic is QUIC over UDP between brokers, control planes and
agents: many connections, small and large messages, and writes that wait
for replicas. The network stack is tuned for that, and every tuning has to
earn its place with a measurement.

| # | Requirement | Checked by | From |
|---|---|---|---|
| N1 | **No slower than the hardware.** Felix's benchmark matrix run on NineLives matches or beats the same hardware running a general-purpose distribution, on throughput and latency, for every cell. A loss blocks the change that caused it. | The matrix, run on the image ([#58](https://github.com/GetFelix/ninelives/issues/58)) | M2 |
| N2 | **The kernel has what QUIC needs**: UDP GSO and GRO, large socket buffers, BBR with fq, io_uring. | A CI check on the built kernel config, plus a qemu test that Felix's sockets get them | M0 |
| N3 | **Every tuning is measured.** A sysctl, qdisc, IRQ or CPU setting goes in with the benchmark result that justifies it, and comes out if a later measurement says it no longer helps. Folklore settings are not accepted. | Review | M1 |
| N4 | **Workloads cannot starve the log.** On a shared machine, Felix's network and disk threads keep their CPU and memory reservation under a busy workload. | A qemu or cloud test that saturates a workload and checks Felix's latency | M2 |
| N5 | **Failover is measured.** The three-VM demo reports how long a workload took to resume, every run. | The M2 demo ([#7](https://github.com/GetFelix/ninelives/issues/7)) | M2 |
