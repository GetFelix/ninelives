# Security and networking requirements

NineLives has to be very secure and fast on the network. These are the
requirements that make those two words checkable. Every decision record,
component design and kernel config change is measured against them, and
each one says how it is checked and from which milestone.

When a security requirement and a performance requirement conflict,
security wins, unless a decision record accepts a measured cost and says
why.

A requirement is changed through a pull request that says why, like a
decision record.

## Security

The threat model (who the attacker is, what they can reach, what we
protect) is written before M1
([#56](https://github.com/GetFelix/ninelives/issues/56)). It decides the
questions this page leaves open: encryption at rest, authenticated time,
and CPU microcode and mitigation policy. These hold whatever it finds.

| # | Requirement | Checked by | From |
|---|---|---|---|
| S1 | **Minimal surface.** A production image contains only what a listed requirement or component needs, and no interactive shell, remote login, compiler or package manager. Packages Debian requires that cannot be removed are listed in the inventory with the reason. A development image may add a serial console shell and says so in its name. | The image inventory is diffed against an allowlist in CI | M0 |
| S2 | **A hardened kernel with no loadable modules.** The built config passes [kernel-hardening-checker](https://github.com/a13xp0p0v/kernel-hardening-checker), with every option it flags either fixed or listed in `kernel/` with the reason. | That tool, in CI, on every kernel build | M0 |
| S3 | **Read-only root.** Nothing on the root changes at run time. State lives only on the data partition. | qemu test: writes to the root fail | M0 |
| S4 | **Every shipped binary is pinned and verified**: by hash and upstream signature for components ([0004](decisions/0004-components-from-pinned-releases.md)), from a pinned snapshot for packages ([0006](decisions/0006-debian-base.md)), and from a signature-checked tarball for the kernel ([0007](decisions/0007-kernel-from-upstream-source.md)). | The image build fails closed | M0 |
| S5 | **Reproducible images.** The same commit builds the same bytes, including on a clean runner with a different build path and time. No cache is used for the comparison build. Signatures are detached and the comparison is made before signing. | CI builds twice, the second time clean, and compares | M0 |
| S6 | **Each service is isolated**: its own user, its own cgroup, no ambient capabilities, a seccomp filter, and Landlock limits on the files it may touch. Kernel interfaces that bypass seccomp (io_uring) are compiled out or disabled unless a decision record enables them for a named service. | The init's tests, per service | M1 |
| S7 | **Only listed ports are open**: the Felix services the role runs, the join, and the authenticated diagnostics interface (S11), each on the interfaces the role needs. The list lives in the image config. Everything else is dropped by default. | qemu test scans the machine from outside | M1 |
| S8 | **Secrets stay where they belong.** Credentials live on the data partition, readable only by the service that needs them. They are never written to the root, to console or service output, or to Felix streams. | A qemu test plants known test credentials and searches the root, captured output and the cluster's streams for them | M1 |
| S9 | **Patch deadline.** Once an upstream fix is available (Debian's security archive, a kernel.org stable release for the pinned series, or a Felix or felixd release), a fixed image is built within 72 hours for fixes Debian or the upstream rates critical or high, and within 7 days for others. Issues Debian does not fix in its security archive follow its point releases. | Advisory feeds open pull requests automatically ([#57](https://github.com/GetFelix/ninelives/issues/57)); time from fix to built image is tracked | M1 for building; M3 for running machines, when upgrades exist |
| S10 | **Authenticated transport.** All traffic between machines (Felix, felixd, the join) is encrypted and authenticated with credentials from the join. Nothing listens unauthenticated, metrics included. | qemu test: a machine without credentials gets no data and cannot write | M1 ([#16](https://github.com/GetFelix/ninelives/issues/16)) |
| S11 | **A production machine can be diagnosed without a shell.** Service output and init state are kept on the data partition with a size bound, and can be read through an authenticated interface. Kernel crash dumps are kept on the data partition. | qemu test: crash a service and the kernel, read both back | M1 |
| S12 | **Published images come with what is needed to check them**: an SBOM in SPDX built from the inventory, a build provenance attestation, and the source for every GPL component. | The release workflow fails if any is missing | First published image |
| S13 | **Verified boot and no rollback.** The firmware checks a signed kernel image, the kernel checks the root against a dm-verity hash, and a machine refuses an image older than its minimum version. Signing keys are kept outside CI's reach, and how they are rotated is written down. | qemu test with secure boot on: a modified or rolled-back image does not boot | M3 ([#9](https://github.com/GetFelix/ninelives/issues/9)) |
| S14 | **A way to report vulnerabilities**, in [SECURITY.md](../SECURITY.md). | Exists | Now |

## Networking

Felix's traffic is QUIC over UDP between brokers, control planes and
agents: many connections, small and large messages, and writes that wait
for replicas. QUIC's congestion control runs in Felix's process, not the
kernel, so the kernel's job is to move UDP datagrams in batches and stay
out of the way.

| # | Requirement | Checked by | From |
|---|---|---|---|
| N1 | **No slower than a general-purpose system.** Felix's benchmark matrix run on NineLives matches the same cloud machines running Debian 13 with its stock kernel and Felix's documented tuning, on throughput and latency, for every cell, within the run-to-run noise band the matrix measures. Both runs use the same Felix settings; if the baseline gains from a kernel feature NineLives compiles out (io_uring, for Felix's optional flusher), that is reported as its own cell and decided by a decision record, not hidden. A cell outside the band blocks the change that caused it. | The matrix on identical cloud machines ([#58](https://github.com/GetFelix/ninelives/issues/58)) | M2 |
| N2 | **The kernel gives Felix's QUIC stack what it uses**: UDP segmentation and receive offload (GSO and GRO) and batched send and receive. | qemu test: GSO and GRO are available on the image's UDP sockets (probed with `UDP_SEGMENT` and `UDP_GRO`); a Felix-level check once Felix reports them | M1 |
| N3 | **Every tuning is measured.** A sysctl, qdisc, socket buffer, IRQ or CPU setting goes in with the benchmark result that justifies it, and comes out if a later measurement says it no longer helps. Nothing goes in because it is commonly recommended. | Review | M1 |
| N4 | **Workloads cannot starve the log.** On a shared machine, Felix's p99 publish latency under a workload saturating CPU and memory stays within the noise band of the same run without that workload. | A cloud test with a saturating workload | M2 |
| N5 | **Failover is measured.** The three-VM demo reports how long a workload took to resume, every run, kept as a CI artifact and appended to a history file in the repo's perf data. Once ten runs exist, a run more than twice the median of the last ten fails; before that, results are reported only. | The M2 demo ([#7](https://github.com/GetFelix/ninelives/issues/7)) | M2 |
