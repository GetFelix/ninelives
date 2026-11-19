# kernel configuration

The kernel is stock upstream Linux, built from verified source with no
patches and no loadable modules ([0001](../decisions/0001-linux-kernel.md),
[0007](../decisions/0007-kernel-from-upstream-source.md)). This page says
which release, how its config is produced and checked, what is built in and
left out and why, what the command line and sysctls are, and how a booted
image proves all of it. Nothing here is built yet; the work is
[#3](https://github.com/GetFelix/ninelives/issues/3).

The rule behind every choice: a service that gets code execution is assumed
to try a kernel exploit next ([threat model](../threat-model.md#how-to-read-it)).
Code that is not compiled in cannot be exploited, and a check that a seccomp
filter forgot is still made by the kernel if the feature is absent. So the
config is an allowlist: everything is off unless a line in a fragment turns
it on and says which requirement needs it.

## What it does

It defines the kernel every NineLives image boots: one pinned upstream
long-term release, built with Clang from a config that starts from
`allnoconfig` and adds only what a listed requirement, the init, Felix or a
target's hardware needs. It sets the command line words and boot-time
sysctls, so the kernel is locked down in confidentiality mode, cannot load
code, offers no unprivileged namespaces, BPF, io_uring or userfaultfd, and
has no packet path a process can rewrite except the nftables ruleset loaded
at boot. CI resolves the config, compares it with the reviewed resolved
config and a policy file, and runs kernel-hardening-checker on it; a qemu
test reads the effective state back from the running kernel.

## What it does not do

- **No patches and no out-of-tree code.** A feature that needs a patch is a
  reason to reopen [0001](../decisions/0001-linux-kernel.md), not a patch.
- **No loadable modules, ever.** New hardware is a config change and a new
  image ([0007](../decisions/0007-kernel-from-upstream-source.md)).
- **No kernel tuning without a measurement.** Network sysctls, qdiscs, IRQ
  and CPU settings belong to the network design and N3. This page sets only
  security sysctls and the few the init's design depends on.
- **No crash memory dumps.** kexec is compiled out, so kdump cannot exist.
  What the kernel keeps after a panic is the log (see
  [Open questions](#open-questions)).
- **No profiling or tracing in a shipped image.** No ftrace, kprobes,
  debugfs or BPF. A profiling kernel for investigating a performance gap is
  a separate build that never ships and is never used for an N1 result.
- **No per-image or per-machine variation beyond the target.** Every image
  for one target and architecture boots the same bytes and the same command
  line. A development image differs in what the root contains, not in the
  kernel.
- **Not the verified boot design.** The root boots through dm-verity from M0
  ([image design](image.md#boot-without-an-initrd)); signing the kernel, the
  command line and the root hash is M3
  ([#9](https://github.com/GetFelix/ninelives/issues/9)). This page lists
  only what the kernel must build in for both.
- **Not the exact command line.** The image design owns it. This page lists
  the parameters the kernel policy requires, and CI checks the image's
  command line contains them (see [The command line](#the-command-line)).

## Interfaces

### The release and how it moves

**The pinned line is 6.18**, at **6.18.64** (released 2026-11-17).
kernel.org projects support for 6.18 until December 2028 (checked against
[kernel.org's releases page](https://www.kernel.org/category/releases.html)
on 2026-11-18).

Why 6.18 and not 6.12, which has the same projected end of life (December
2028, same page) and is the series Debian 13 ships:

- kCFI under its final name (`CONFIG_CFI`), FineIBT with the branch history
  variant, and the newer slab hardening, all in upstream's
  `hardening.config` fragment for this series.
- A year of point releases already behind it, so it is not a fresh series.

Landlock is not a reason: the ABI 6 scopes the init depends on (signal and
abstract socket scoping) are in both, and ABI 7's addition, denial logging,
goes through audit, which is off (see [Open questions](#open-questions)).

The cost: N1 compares against Debian 13's stock 6.12 kernel, so a gap in
the matrix can come from the version as well as the config. A cell that
fails gets a diagnostic run that separates the two (see
[Test plan](#test-plan)).

`kernel/VERSION` holds the version and the tarball's SHA-256. The build
downloads the tarball, checks its PGP signature against the fingerprints in
`kernel/keys/` and the hash in `kernel/VERSION`, and fails closed on
either (S4, [0007](../decisions/0007-kernel-from-upstream-source.md)).

**Point releases.** Every stable release of the pinned line is taken.
Upstream does not rate kernel fixes by severity and assigns CVEs to most of
them, so there is no way to pick only the security ones. The advisory job
([#57](https://github.com/GetFelix/ninelives/issues/57)) opens a pull
request for each release; it is built and tested like any kernel change and
merged within S9's 72 hours. A point release that changes the resolved
config (rare, but it happens when a fix adds an option) shows up as a diff
and is reviewed line by line.

**Moving to the next long-term line** is one pull request, made after
kernel.org lists the new line as longterm and it has had at least three
months of point releases, and no later than twelve months before the pinned
line's end of life. That pull request carries the resolved config diff with
every new option reviewed, the kernel-hardening-checker result, the qemu
tests on both architectures, the durability tests from
[0001](../decisions/0001-linux-kernel.md) (Felix's `fsync`, `pread` and
preallocation assumptions), and an N1 run. Nothing else goes in the same
pull request.

### How the config is produced

Base: upstream `allnoconfig` for the architecture, then upstream's
`kernel/configs/hardening.config`, then our fragments, then
`olddefconfig`, all with `LLVM=1`.

Why not a defconfig. With `CONFIG_MODULES=n`, Kconfig treats every tristate
option as a boolean, so every `=m` in a defconfig would be built into the
kernel; arm64's defconfig alone would bring in drivers for hundreds of
boards. A defconfig also turns on whole subsystems that would each need an
`=n` line, and every new release adds defaults that someone has to notice.
Starting from `allnoconfig` makes the fragments an allowlist. The price is a
longer fragment set (a few hundred lines across all files), each line
justified.

```
kernel/
  VERSION                     6.18.64 and the tarball's SHA-256
  keys/                       stable maintainers' fingerprints (0007)
  fragments/
    common.config             both architectures, every target
    x86_64.config             architecture hardening and CPU support
    aarch64.config
    qemu.config               M0 to M2 targets' drivers
    <cloud>.config            one per M5 target
  resolved/
    qemu-x86_64.config        the full resolved .config, committed
    qemu-aarch64.config
  policy.toml                 options that must be set or unset, expected
                              sysctls, required and forbidden command line
                              words; a common table and one per architecture
  hardening-exceptions.toml   each kernel-hardening-checker finding we do
                              not fix, with the reason
```

Every line in a fragment carries a comment naming what needs it: a
requirement (`S6`), a component (`init: signalfd`), or a target
(`qemu: virtio-blk root`). A line with no reason fails CI. Example:

```
# S6, init: Landlock per service; signal and socket scoping need ABI 6.
CONFIG_SECURITY_LANDLOCK=y
# S2: no runtime code loading. Everything the image needs is built in.
# CONFIG_MODULES is not set
```

`allnoconfig` turns off every option it can see, including many a working
system needs that are hidden behind `CONFIG_EXPERT`. `common.config` sets
`CONFIG_EXPERT=y` and lists each one it needs, so these are explicit rather
than inherited. The EXPERT options, and who needs each:

| On | Needed by |
|---|---|
| `MULTIUSER`, `POSIX_TIMERS`, `PRINTK`, `BUG` | everything: uids, `timer_create`, a kernel log, `BUG()` that stops instead of running on |
| `FUTEX`, `EPOLL`, `EVENTFD`, `SIGNALFD`, `TIMERFD` | Felix and felixd (tokio's reactor, Rust's locks), the init's loop |
| `FILE_LOCKING` | Felix: `flock` on each log directory (`felix-storage`'s `disk_log/dir_lock.rs`) |
| `SHMEM` (with `TMPFS`, `MEMFD_CREATE`) | `/run`, `/tmp`, memfds |
| `ADVISE_SYSCALLS` | `madvise` and `fadvise`, used by the Rust allocators and Felix's segment reads |
| `MEMBARRIER`, `RSEQ` | glibc registers rseq for every thread; Rust's runtime and wasmtime use membarrier |

| Off | Why |
|---|---|
| `UID16`, `SGETMASK_SYSCALL`, `SYSFS_SYSCALL`, `FHANDLE`, `AIO`, `IO_URING`, `KALLSYMS`, `CACHESTAT_SYSCALL`, `PCSPKR_PLATFORM` | Unused interfaces; most are in the tables below |
| `ELF_CORE` | Goes with `COREDUMP=n` |

The visible non-EXPERT options `allnoconfig` turns off are handled the same
way: what Felix, felixd and the init need is listed with its user
(`INOTIFY_USER` only if a service watches files, for example). This table
is a reading of the code, not proof. The proof is a test: the image design
records a syscall trace of every service from a qemu boot and checks each
seccomp profile against it ([image design](image.md#service-profiles),
[init design](init.md#the-config)). This page adds two uses of
that trace. Any traced call that returned `ENOSYS`, or `EINVAL` for an
unknown flag, fails the build, because it names an option we compiled out
that something uses. And Felix's own test binaries (`felix-storage`,
`felix-broker` and the QUIC tests, built for the image's target) run on the
built kernel in qemu, which reaches calls a five-minute service run may not.

**The compiler is Clang** (`LLVM=1`), from the pinned Debian 13 snapshot
([0006](../decisions/0006-debian-base.md)). kCFI and arm64's shadow call
stack need it, and one toolchain for both architectures keeps the
reproducible build simpler. The Rust toolchain is not involved:
`CONFIG_RUST` is off.

**Reproducibility** (S5): `KBUILD_BUILD_TIMESTAMP`, `KBUILD_BUILD_USER`,
`KBUILD_BUILD_HOST` and `LOCALVERSION` are fixed, and nothing in the config
injects build-time randomness. That is why `CONFIG_RANDSTRUCT` is off: its
seed would have to be fixed and published for the build to reproduce, and
a published seed gives an attacker the layout. It is listed as a
hardening-checker exception.

### What CI checks

On every pull request that touches `kernel/` or the image build, and on
every point-release bump:

1. **Resolved config matches.** CI regenerates the config and compares it
   byte for byte with `kernel/resolved/<target>-<arch>.config`. A change in
   any fragment, or a new upstream default, fails until the resolved file is
   updated in the same pull request, so the reviewer sees the full effect.
2. **Every fragment line landed.** An option a fragment sets that the
   resolved config does not contain as written (a missing dependency, an
   option renamed upstream) fails. Lines in upstream's `hardening.config`
   that cannot apply here are listed in `policy.toml` with the reason (for
   example `STRICT_MODULE_RWX`, which needs modules).
3. **Policy holds.** `policy.toml` lists options that must be set and must
   be unset. The must-be-unset list is the attack surface this page removes
   (modules, io_uring, BPF syscall, user namespaces, kexec, `/dev/mem`,
   debugfs, xtables, tc, and the rest named below). It is checked
   separately from the resolved diff so that removing one cannot slip
   through as part of a large diff.
4. **kernel-hardening-checker passes** on the resolved config, the command
   line and the expected sysctls, with every finding either fixed or listed
   in `hardening-exceptions.toml` (S2). Its version is pinned (S21).
5. **The command line carries the kernel policy.** The image design owns
   the exact command line and checks it byte for byte. This check reads
   that line from the built UKI and fails unless it contains every word
   `policy.toml` requires for the architecture (see
   [The command line](#the-command-line)). It also rejects any word that
   turns a protection off: `mitigations=off` or any per-mitigation `=off`,
   `nokaslr`, `nopti`, `lsm=`, `security=`, `lockdown=`, `init_on_alloc=0`,
   `init_on_free=0`, `hardened_usercopy=off`, `slab_merge`, `iommu=pt`,
   `module_blacklist`, and a `sysctl.` word that differs from `policy.toml`.

### Hardening, by what it stops

Each group names the threat it addresses. Threat and requirement numbers
are from the [threat model](../threat-model.md) and
[requirements](../requirements.md).

#### No new kernel code at run time (T16, S2)

| Setting | Why |
|---|---|
| `CONFIG_MODULES=n` | Nothing can be loaded into the kernel. With no modules, module signing, `modules_disabled` and module autoloading (including line discipline and netdev autoload) do not exist. |
| `CONFIG_KEXEC=n`, `CONFIG_KEXEC_FILE=n`, `CONFIG_CRASH_DUMP=n`, `CONFIG_KEXEC_HANDOVER=n` | kexec replaces the running kernel with any image root supplies, which undoes verified boot from inside. The cost is no kdump (see S11 under [Open questions](#open-questions)). |
| `CONFIG_HIBERNATION=n`, `CONFIG_SUSPEND=n` | Hibernation writes all memory, credentials included, to disk and reads it back as trusted kernel state. The init has no suspend. |
| `CONFIG_BINFMT_MISC=n`, `CONFIG_BINFMT_SCRIPT=n` | No interpreter registration and no `#!` scripts. There is no shell or interpreter in the image (S1), so a script on any mount cannot run. |
| `CONFIG_STATIC_USERMODEHELPER=y` with an empty path, `CONFIG_UEVENT_HELPER=n` | The kernel never starts a user program on its own (core dump pipes, `request-key`, hotplug, `poweroff_cmd`). Each of those helpers would run as full root. One consequence: with an empty helper path, `call_usermodehelper` returns 0 without running anything, so `orderly_poweroff` thinks it succeeded and never falls back to a forced power-off. A host shutdown request that arrives that way (Hyper-V's `hv_utils`, Xen's shutdown watch) does nothing. The M5 targets on those hypervisors need another path. The power button is not affected: on both architectures it is an ACPI button that reports `KEY_POWER` to PID 1 as an input event, with no helper involved. |
| `CONFIG_COREDUMP=n` | See [Core dumps](#core-dumps). |

#### Lockdown in confidentiality mode (T16, T22, T47)

`CONFIG_SECURITY_LOCKDOWN_LSM=y`, `CONFIG_SECURITY_LOCKDOWN_LSM_EARLY=y`,
`CONFIG_LOCK_DOWN_KERNEL_FORCE_CONFIDENTIALITY=y`.

Integrity mode stops user space from modifying the running kernel: `/dev/mem`
and friends, unsigned kexec, MSR writes, `ioperm`, ACPI table overrides, PCI
BAR access through sysfs, module parameters that set hardware addresses.
Confidentiality mode adds the ways to *read* kernel memory: `/proc/kcore`,
kprobes, BPF and perf reads of kernel data, tracefs, and dumping xfrm
secrets. On a machine where every service's credentials and every tenant's
records pass through kernel memory, reading it is as good as writing it.
The level is set by config, before any user process runs, and cannot be
lowered afterwards. Most of what lockdown blocks is also compiled out below;
lockdown is the second check for anything that is not.

[0007](../decisions/0007-kernel-from-upstream-source.md) says integrity mode.
This page proposes confidentiality instead; the decision record needs the
change.

#### Interfaces that bypass or widen the sandbox (T16, S6)

| Setting | Why |
|---|---|
| `CONFIG_IO_URING=n` | io_uring performs file and network operations that seccomp never sees, and has been one of the largest sources of local privilege escalation. S6 and [0007](../decisions/0007-kernel-from-upstream-source.md). |
| `CONFIG_BPF_SYSCALL=n` | No `bpf(2)` at all, so no unprivileged eBPF, no verifier to attack, no BPF LSM, no XDP. `kernel.unprivileged_bpf_disabled` does not exist and is not needed. Classic BPF stays: seccomp filters and socket filters are built on it. |
| `CONFIG_BPF_JIT=y`, `CONFIG_BPF_JIT_ALWAYS_ON=y`, `net.core.bpf_jit_harden=2` | Classic BPF programs (seccomp filters, socket filters any process can attach) are JIT-compiled with constant blinding, and the interpreter, a known speculative execution gadget, is not built. |
| `CONFIG_USER_NS=n` | Unprivileged user namespaces hand any process `CAP_NET_ADMIN` and `CAP_SYS_ADMIN` over objects it creates, which has exposed nf_tables, mount and filesystem code to unprivileged users for years. Compiled out, nobody can create one. Ubuntu and Debian restricted unprivileged user namespaces for the same reason: they were the first step of many privilege-escalation chains. |
| No network, IPC, UTS or time namespaces (`NET_NS`, `IPC_NS`, `UTS_NS`, `TIME_NS` off) | Nothing in the image uses them: the init isolates services with uids, cgroups, Landlock and seccomp ([init design](init.md#what-it-does-not-do)). |
| `CONFIG_PID_NS=y`, kept for one sysctl | `vm.memfd_noexec` (below) is built only with PID namespaces. Creating one needs `CAP_SYS_ADMIN`, which no process holds after the drop, and with no user namespaces there is no way to get it. Mount and cgroup namespaces cannot be compiled out and need the same capability. |
| `CONFIG_USERFAULTFD=n` | userfaultfd lets a process stall the kernel at a chosen page fault, which turns narrow race conditions into reliable exploits. Nothing in the image uses it. Stronger than `vm.unprivileged_userfaultfd=0`. |
| `CONFIG_KEYS=n` | No `add_key`, `keyctl` or `request_key`. Nothing uses the kernel keyring until M3 may need it for S16's sealed key (see [Open questions](#open-questions)). |
| `CONFIG_SYSVIPC=n`, `CONFIG_POSIX_MQUEUE=n`, `CONFIG_CHECKPOINT_RESTORE=n`, `CONFIG_KCMP=n`, `CONFIG_FHANDLE=n`, `CONFIG_SYSFS_SYSCALL=n`, `CONFIG_USELIB=n`, `CONFIG_UID16=n`, `CONFIG_AIO=n`, every `CRYPTO_USER_API_*` option (no `AF_ALG`) | Unused system calls and socket families with a history of bugs. The qemu test that runs every real service under its real profile for five minutes ([init design](init.md#test-plan)) catches anything we were wrong about. |
| `vm.memfd_noexec=2` | A memfd can never be made executable, so a service cannot run code from memory and bypass the `noexec` data partition. Landlock does not cover this: its rules are about paths, and a memfd lives on an internal mount it does not check. |

On x86_64 also: `CONFIG_IA32_EMULATION=n` and `CONFIG_X86_X32_ABI=n` (one
system call ABI, so a seccomp filter has one table to get right),
`CONFIG_MODIFY_LDT_SYSCALL=n`, `CONFIG_X86_IOPL_IOPERM=n`,
`CONFIG_X86_MSR=n`, `CONFIG_X86_CPUID=n`, `CONFIG_LEGACY_VSYSCALL_NONE=y`.
On aarch64, `CONFIG_COMPAT=n`.

#### Reading other processes (T17, T67)

| Setting | Why |
|---|---|
| `CONFIG_SECURITY_YAMA=y`, `kernel.yama.ptrace_scope=3` | No process can `ptrace` another, whatever its uid or capabilities, and the value can never be lowered. Yama's check covers every attach-mode access, so `process_vm_readv`, `process_vm_writev`, opening `/proc/<pid>/mem` and `pidfd_getfd` are refused too. Debugging a live process needs a test image with a different command line, which is a different image. |
| `CONFIG_PROC_MEM_NO_FORCE=y` | A process's own `/proc/self/mem` writes obey page permissions, so it cannot patch its read-only code pages (the `FOLL_FORCE` override). That would bypass `noexec` and W^X the same way an executable memfd would. |
| `fs.suid_dumpable=0` | A process that changed credentials stays non-dumpable, so its `/proc` files stay root-owned. There are no setuid binaries (S24); this covers the launchers' uid change. |
| `fs.protected_symlinks=1`, `fs.protected_hardlinks=1`, `fs.protected_fifos=2`, `fs.protected_regular=2` | A service cannot plant a link or FIFO in a shared directory (`/run`, `/tmp`) that another uid then follows or opens. |
| `CONFIG_SECCOMP=y`, `CONFIG_SECCOMP_FILTER=y` | Each service's filter (S6). |
| `CONFIG_SECURITY_LANDLOCK=y` | Each service's file, signal and socket rules (S6). The init needs ABI 6 or later. |

**LSMs and their order.** `CONFIG_LSM="landlock,lockdown,yama"`, with the
capability module always first. These LSMs only deny, and every enabled LSM
must allow an operation, so the order does not change any outcome; it
decides which module refuses first and so which error a caller sees. The
string does decide which LSMs run at all: one built in but missing from it
is off. That is why the command line may not carry `lsm=` or `security=`.
No SELinux, AppArmor, Smack, TOMOYO or BPF LSM: the init's policy is uids,
Landlock and seccomp, and a MAC policy nobody writes is surface without
protection. LoadPin and SafeSetID are off too: with no modules and no kexec,
LoadPin would guard only firmware loads from the read-only root, and
nothing changes uid after the drop. IPE (only run what the dm-verity root
holds) is the likely addition in M3 and is an open question.

#### Information that makes exploits reliable (T16)

| Setting | Why |
|---|---|
| `CONFIG_KALLSYMS=n` | No `/proc/kallsyms`. Panics print raw addresses and the KASLR offset, and are symbolised offline from the `System.map` the reproducible build keeps for each release. |
| `kernel.kptr_restrict=2` | Kernel pointers printed with `%pK` show as zero to everyone. |
| `CONFIG_SECURITY_DMESG_RESTRICT=y` | Reading the kernel log needs `CAP_SYSLOG`, which no process holds after the drop. The serial console still carries it (S11). |
| `CONFIG_DEBUG_FS=n`, `CONFIG_FTRACE=n`, `CONFIG_KPROBES=n`, `CONFIG_UPROBES=n`, `CONFIG_PROFILING=n`, `CONFIG_PROC_KCORE=n`, `CONFIG_MAGIC_SYSRQ=n`, `CONFIG_KGDB=n` | Debug and tracing interfaces leak layout and often allow writes. SysRq would let anyone at the console dump memory or crash the machine. |
| `CONFIG_DEVMEM=n`, `CONFIG_DEVPORT=n` | No `/dev/mem` or `/dev/port`. `/dev/kmem` was removed upstream in 5.13; nothing to set. |
| `CONFIG_ACPI_CUSTOM_METHOD=n`, `CONFIG_ACPI_TABLE_UPGRADE=n`, `CONFIG_ACPI_DEBUGGER=n`, `CONFIG_EFIVAR_FS=n` | Ways to run or install firmware-level code, or to write firmware variables, from user space. |
| `perf_event_open` | x86_64 selects `CONFIG_PERF_EVENTS` unconditionally, so there it stays, with `kernel.perf_event_paranoid=2` (the strictest level upstream defines; `3` is a distribution patch), lockdown blocking kernel reads, and every service's seccomp filter denying the call. On aarch64 `CONFIG_PERF_EVENTS=n`. |
| `CONFIG_SWAP=n` | No swap, so memory holding credentials is never written to disk. |

#### Memory corruption hardening (T16)

Most of this comes from upstream's `hardening.config`; the fragment states
it again where we depend on it, so a change upstream does not quietly
remove it.

| Purpose | Settings |
|---|---|
| Stack overflows | `STACKPROTECTOR_STRONG`, `VMAP_STACK` (guard pages), `SCHED_STACK_END_CHECK`, `RANDOMIZE_KSTACK_OFFSET_DEFAULT`, `INIT_STACK_ALL_ZERO`, `KSTACK_ERASE` where the compiler supports it, `ZERO_CALL_USED_REGS` |
| Copies between user and kernel memory | `HARDENED_USERCOPY`, `FORTIFY_SOURCE`, `UBSAN_BOUNDS` with `UBSAN_TRAP` |
| Heap use-after-free and info leaks | `INIT_ON_ALLOC_DEFAULT_ON`, `INIT_ON_FREE_DEFAULT_ON`, `SLAB_FREELIST_RANDOM`, `SLAB_FREELIST_HARDENED`, `SLAB_BUCKETS`, `RANDOM_KMALLOC_CACHES`, `SLAB_MERGE_DEFAULT=n`, `SHUFFLE_PAGE_ALLOCATOR` with `page_alloc.shuffle=1`, `KFENCE` |
| Corrupted kernel data structures | `LIST_HARDENED`, `BUG_ON_DATA_CORRUPTION`, `PAGE_TABLE_CHECK_ENFORCED`, `DEBUG_WX`, `STRICT_KERNEL_RWX` |
| Address layout | `RANDOMIZE_BASE`, `RANDOMIZE_MEMORY` (x86_64), `vm.mmap_rnd_bits` at the architecture's maximum, `DEFAULT_MMAP_MIN_ADDR=65536` |
| Forward-edge control flow | `CFI` (kCFI, Clang), `CFI_PERMISSIVE=n`; on x86_64 `X86_KERNEL_IBT` so FineIBT replaces kCFI on CPUs with IBT; on aarch64 `ARM64_BTI_KERNEL` |
| Return addresses | aarch64: `SHADOW_CALL_STACK` and `ARM64_PTR_AUTH_KERNEL`. x86_64 has no kernel shadow stack upstream: return addresses there are protected only by the stack protector and the return thunks of the CPU mitigations. We say so rather than imply otherwise. |
| aarch64 extras | `ARM64_MTE` (used where the CPU has it), `ARM64_E0PD`, `UNMAP_KERNEL_AT_EL0` |
| Hostile DMA (T54) | `IOMMU_SUPPORT`, `IOMMU_DEFAULT_DMA_STRICT`, `INTEL_IOMMU_DEFAULT_ON`, `AMD_IOMMU`, `EFI_DISABLE_PCI_DMA`. In qemu with no virtual IOMMU this is inert; it matters on bare metal (M5). |

KASLR is a speed bump, not a wall: side channels against it are public on
both architectures. Removing kallsyms, debugfs and pointer leaks takes away
the easy ways; it does not make the layout secret from a determined local
attacker.

On aarch64, KASLR needs a seed from the firmware. Without one the kernel
boots unrandomised and says so on the console; the qemu test fails on that
message (K12).

#### The packet filter, and nothing that rewrites packets after it (T18, T70, S7, S18)

S7 and S18 are enforced by one nftables ruleset loaded at boot. The kernel
side has three jobs: offer nftables and nothing else, keep the netfilter
netlink family to the one process that loads the ruleset, and leave no
other way for a holder of `CAP_NET_ADMIN` to filter, rewrite or redirect
traffic. These follow the netfilter/tc rule in S2 or S7, pending.

**Built in, nothing more:**

| Setting | Why |
|---|---|
| `NETFILTER`, `NF_TABLES`, `NF_TABLES_INET`, `NF_TABLES_IPV4`, `NF_TABLES_IPV6` | The ruleset. `meta skuid` and `skgid`, which S18 matches per service, are part of the nf_tables core. |
| `NF_CONNTRACK`, `NFT_CT` | `ct state established,related` for replies. |
| `NFT_REJECT`, `NFT_REJECT_INET`, `NFT_LIMIT` | Refuse unlisted traffic visibly to local senders, and rate-limit. |
| `NETFILTER_NETLINK` | Selected by `NF_TABLES`; the interface the loader uses. |

**Off:** every legacy xtables option (`NETFILTER_XTABLES`, its `_LEGACY`
and `_COMPAT` variants, `IP_NF_IPTABLES`, `IP6_NF_IPTABLES`,
`IP_NF_ARPTABLES`, `BRIDGE_NF_EBTABLES` and their `_LEGACY` options), so
nftables is the only packet filter and `nft_compat` cannot be built;
`NF_TABLES_ARP`, `NF_TABLES_BRIDGE`, `NF_TABLES_NETDEV`; NAT; every
conntrack helper (in-kernel parsers for FTP, SIP and others); `NF_CT_NETLINK`,
`NETFILTER_NETLINK_QUEUE`, `NETFILTER_NETLINK_LOG`, `NETFILTER_NETLINK_ACCT`,
`NETFILTER_NETLINK_OSF`.

**Who can reach `NETLINK_NETFILTER`.** nfnetlink accepts a message only from
a sender with `CAP_NET_ADMIN` in the network namespace's user namespace.
With no user namespaces that is the initial one, so the only candidates are
processes holding `CAP_NET_ADMIN`: the loader that runs once at boot and
exits, and the DHCP client (S6). The DHCP client's seccomp filter refuses
`socket(AF_NETLINK, *, NETLINK_NETFILTER)` by matching the first and third
arguments. The kernel has no way to freeze a ruleset, so this is the whole
guarantee: after the loader exits, no process holds both `CAP_NET_ADMIN`
and a filter that lets it open that family. Whether the loader is PID 1
before the drop or the phase-0 service is the network design's call; the
kernel side is the same.

**Traffic control is compiled out.** `CONFIG_NET_SCHED=n`. That removes
the tc netlink interface entirely (`sch_api`, `cls_api`, `act_api` are not
built), so `RTM_NEWQDISC`, `RTM_NEWTFILTER` and `RTM_NEWACTION` have no
handler, and no classifier or action can exist: in particular `act_pedit`,
`act_nat` and `act_mirred`, which a `CAP_NET_ADMIN` holder could otherwise
use over `NETLINK_ROUTE` to rewrite or mirror traffic after netfilter has
passed it. The ingress and clsact hooks (`NET_XGRESS`) go with them.

What stays of tc is what upstream builds unconditionally and cannot be
configured without `NET_SCHED`:

| Piece | Why it stays |
|---|---|
| `sch_generic`: `pfifo_fast` and `noqueue` | The default queue on each transmit queue, and on loopback. Always built; with no tc interface, nothing can replace or reconfigure it. |
| `sch_mq` | The root that gives a multiqueue device (virtio-net with several queues) one `pfifo_fast` per queue. Always built. |
| `sch_frag` | Built with `INET`; it is only called from tc actions and Open vSwitch, neither of which exists here. |

If N1 shows that a better queue discipline (`fq` or `fq_codel`) matters for
Felix, `NET_SCHED` comes back with exactly that qdisc and still no
classifiers or actions (`NET_CLS=n`, `NET_CLS_ACT=n`). That reopens the
qdisc netlink messages to `CAP_NET_ADMIN` holders, so it needs a decision
record.

**Other ways to redirect traffic, also off:** `TUN` (a tap device turns
routed traffic into a file a process reads), `BRIDGE`, `VLAN_8021Q`,
`MACVLAN`, `IPVLAN`, `VETH`, `VXLAN`, `GENEVE`, every IP tunnel,
`WIREGUARD`, `XFRM` (IPsec policies can redirect and encrypt traffic
elsewhere), `MPLS`, `FIB_RULES` (policy routing), `LWTUNNEL`, `NET_SHAPER`,
`OPENVSWITCH`. What a `CAP_NET_ADMIN` holder can still change is addresses,
routes, link state and neighbour entries, which the DHCP client needs. A
hostile route can send traffic elsewhere; TLS keeps it unreadable (T70, S10).

Other protocol families are off unless a service uses them: no SCTP, TIPC,
RDS, L2TP, CAN, Bluetooth, wireless, NFC, X.25, AX.25, ATM, `VSOCKETS`, or
`PACKET_DIAG` and the other `*_DIAG` modules. `PACKET` stays because a DHCP
client sends before it has an address, and opening one needs `CAP_NET_RAW`.

#### CPU flaws and microcode (T56, T47, S17)

- `CONFIG_CPU_MITIGATIONS=y` and every `MITIGATION_*` option the pinned
  tree offers for the architecture is on. `policy.toml` lists them, so a new
  one in a point release is noticed. The command line sets none of them off
  (CI check 5).
- `pti=on` forces page table isolation even on CPUs that report they do not
  need it, because isolation also hides the kernel's mappings from
  prefetch-based KASLR attacks. This costs every system call (see
  [What the hardening costs](#what-the-hardening-costs)).
- `CONFIG_X86_INTEL_TSX_MODE_OFF=y`: TSX has carried several data leaks and
  nothing in the image uses it.
- Speculative store bypass and indirect branch protections stay at the
  upstream default (`prctl`), so a process can ask for them. felixd runs
  untrusted WebAssembly in its own address space (B3), which is the case
  those controls exist for; whether felixd asks is an open question for
  felixd, not a kernel default that would slow every service.
- SMT stays on. `nosmt` would halve the CPUs a machine has. On a cloud, the
  guest's view of sibling vCPUs says little about the host's cores, so
  `nosmt` in a guest buys less than it costs. Bare metal (M5) reopens this.
- **Microcode** (x86_64): `CONFIG_MICROCODE` with
  `CONFIG_MICROCODE_LATE_LOADING=n`. There is no initrd
  (`CONFIG_BLK_DEV_INITRD=n`), so microcode is built into the kernel with
  `CONFIG_EXTRA_FIRMWARE`, listing the files from Debian's
  `intel-microcode` and `amd64-microcode` packages (pinned like every
  package). With no initrd, the early loader falls back to
  built-in firmware, so it still loads before the kernel starts PID 1, and
  from M3 the UKI's signature covers it. The cost: a microcode update is a
  kernel rebuild. That is the same pull request, review and tests as a point
  release, and fits S9's 72 hours. In a VM the hypervisor owns microcode and
  the guest's load does nothing; the cloud provider's microcode is in what
  we trust. aarch64 has no OS-loaded microcode.

### Core dumps

`CONFIG_COREDUMP=n`. No process on the machine can produce a core, and
`/proc/sys/kernel/core_pattern` does not exist: the sysctl is registered by
the core dump code, so a write to it fails with `ENOENT`. That is a stronger form of what S24
asks for: the risk is a `core_pattern` that names a pipe (`|`), or in recent
kernels a socket (`@`), whose handler the kernel runs or trusts as root on the
next crash. With core dumps compiled out there is no pattern to point
anywhere. Services already get a hard `RLIMIT_CORE` of 0 from the init.

If a later design (S11) wants service cores, `COREDUMP` comes back with the
pattern fixed on the command line as
`sysctl.kernel.core_pattern=/usr/lib/ninelives/no-core`: a path on the
read-only root, so every write fails, never `|` or `@`, and CI rejects any
other value. Either way the init does not write `core_pattern`: there is
no file to write now, and the command line would set it then. S24's text
needs to follow whichever is chosen.

### The command line

The image design owns the exact line, including `init=`, the console, the
root and the dm-verity table ([image design](image.md#boot-without-an-initrd)).
This page owns the words the kernel policy requires, kept in `policy.toml`
per architecture. CI check 5 fails if the image's line is missing any of
them.

Both architectures:

```
ro panic=10 oops=panic page_alloc.shuffle=1
sysctl.kernel.kptr_restrict=2
sysctl.kernel.yama.ptrace_scope=3
sysctl.net.core.bpf_jit_harden=2
sysctl.vm.memfd_noexec=2
sysctl.fs.suid_dumpable=0
sysctl.fs.protected_symlinks=1
sysctl.fs.protected_hardlinks=1
sysctl.fs.protected_fifos=2
sysctl.fs.protected_regular=2
```

x86_64 adds:

```
pti=on
sysctl.kernel.perf_event_paranoid=2
```

aarch64 adds nothing. `pti=on` has no meaning there (its equivalent,
`UNMAP_KERNEL_AT_EL0`, is set in config), and with `PERF_EVENTS=n`
`kernel.perf_event_paranoid` does not exist, so setting it would only log
an unknown sysctl. `policy.toml`'s expected sysctls are split the same way.

The root is erofs on dm-verity, set up by the kernel from `dm-mod.create`.
That parameter takes each device as `major:minor` or a `/dev` path, not
`PARTUUID=`, so the image's line names the devices that way.

Where a setting has a config default, the config sets it and the command
line does not repeat it: lockdown, `init_on_alloc`, `init_on_free`,
`dmesg_restrict`, `slab_nomerge`, hardened usercopy, IOMMU strict mode,
`mmap_min_addr`. The rest are `sysctl.` words, which the kernel applies
before it starts PID 1, so no process ever sees the defaults and the init
needs to write no sysctl. From M3 the command line is inside the signed kernel
image (S13); before M3 it is as trusted as the kernel file itself (T46).

`panic_on_warn` is deliberately not set. A `WARN` is a kernel bug report,
not proof of corruption, and every machine in a cluster runs the same
kernel: one `WARN` reachable from the network or from a hypervisor event
would then reboot every machine at once and take the quorum with it. An
oops still panics (`oops=panic`). This is listed as a hardening-checker
exception and is reopened with fleet data.

### Read-only `/proc/sys`

Every sysctl is final when PID 1 starts. The init then bind-mounts
`/proc/sys` on itself and remounts the bind read-only (step 7 of
[Boot, as root](init.md#boot-as-root)), before any service runs. After that
no write to `/proc/sys` succeeds for any process (`EROFS`). Undoing the
remount needs `CAP_SYS_ADMIN` in the initial user namespace, which no
process holds after the drop, and with no user namespaces there is no other
one. The `sysctl(2)` system call no longer exists upstream.

This matters even without root: the `net.*` sysctls let any holder of
`CAP_NET_ADMIN` write them (`ip_forward`, `rp_filter`, and the rest), and
the DHCP client holds it. The read-only mount stops that.

### What the init needs from the kernel

From the [init design](init.md):

| The init uses | Config |
|---|---|
| cgroup v2 with `cgroup.kill`, `nsdelegate`, `memory_recursiveprot`, and the cpu, memory, io and pids controllers | `CGROUPS`, `MEMCG`, `CGROUP_PIDS`, `CGROUP_SCHED`, `FAIR_GROUP_SCHED`, `BLK_CGROUP`, `BLK_CGROUP_IOCOST` (for `io.weight`) |
| `clone3` with `CLONE_INTO_CGROUP` and `CLONE_PARENT`, `waitid`, pidfds | core, always built |
| Landlock ABI 6 or later, seccomp filters, `no_new_privs`, securebits, ambient capabilities | `SECURITY_LANDLOCK`, `SECCOMP_FILTER`, `MULTIUSER`; the rest is core |
| `signalfd`, `epoll`, `timerfd`, `eventfd` | `SIGNALFD`, `EPOLL`, `TIMERFD`, `EVENTFD` (each behind `EXPERT`, so listed) |
| Project quotas on the data partition (`prjquota`) | `QUOTA`, `QUOTACTL`, `QFMT_V2` (the format of ext4's internal quota files; without it the `prjquota` mount fails), ext4 |
| `/proc` with `hidepid`, sysfs, devtmpfs, tmpfs for `/run`, cgroup2 | `PROC_FS`, `PROC_SYSCTL`, `SYSFS`, `DEVTMPFS`, `TMPFS` (`SHMEM`); `DEVTMPFS_MOUNT=n` so the init mounts `/dev` with its own options |
| The power button as an input device reporting `KEY_POWER` | `INPUT`, `INPUT_EVDEV`, `ACPI_BUTTON` on both architectures. On aarch64 the button is an ACPI Generic Event Device, which the ACPI core handles. |
| `/dev/watchdog` | `WATCHDOG`, `I6300ESB_WDT`, `SOFT_WATCHDOG` as the fallback, and `WATCHDOG_NOWAYOUT=y`, so no process holding the device can disarm it |
| `reboot(2)` with `LINUX_REBOOT_CMD_CAD_OFF`, `RB_AUTOBOOT`, `RB_POWER_OFF` | core |
| `/sys/kernel/security` for the tests below | `SECURITYFS` |

### Drivers and filesystems for M0 to M2

The targets are qemu's `q35` machine on x86_64 and `virt` on aarch64, both
booting through UEFI.

| Need | x86_64 | aarch64 |
|---|---|---|
| Guest support | `ACPI`, `HYPERVISOR_GUEST`, `PARAVIRT`, `KVM_GUEST` (kvm-clock) | `ACPI`, `ACPI_SPCR_TABLE`. Booted through UEFI, `virt` describes the machine with ACPI tables, not a device tree, as arm64 clouds do. To verify in [#3](https://github.com/GetFelix/ninelives/issues/3): the kernel picks ACPI and the power button reaches PID 1. |
| Firmware boot | `EFI`, `EFI_STUB` | same |
| Console | `SERIAL_8250`, `SERIAL_8250_CONSOLE`; `VT=n`, no framebuffer | `SERIAL_AMBA_PL011`, `SERIAL_AMBA_PL011_CONSOLE` |
| Bus | `PCI`, `VIRTIO_PCI` | `PCI`, `PCI_HOST_GENERIC`, `VIRTIO_PCI` |
| Disk | `VIRTIO_BLK`, `EFI_PARTITION` (GPT, with partition names in uevents for the init) | same |
| Network | `VIRTIO_NET`, with UDP GSO and GRO (N2) from the core | same |
| Entropy | `HW_RANDOM_VIRTIO`, which also seeds the boot | same; aarch64 KASLR also needs the firmware's RNG |
| Clock | `RTC_DRV_CMOS`; `PTP_1588_CLOCK_KVM`, so chrony can use the hypervisor's clock (S15) | `RTC_DRV_PL031`; `PTP_1588_CLOCK_KVM` |
| Power button, watchdog | as in the table above | as above; `i6300esb` is a PCI device and works on `virt` too |

**The root, from M0.** The image design boots an erofs root on dm-verity,
set up by the kernel itself with no initrd
([image design](image.md#boot-without-an-initrd)). So `common.config` has:

| Setting | Why |
|---|---|
| `BLK_DEV_DM`, `DM_VERITY`, `DM_INIT` | dm-verity, created from `dm-mod.create` on the command line before the root is mounted |
| `CRYPTO_SHA256` | The verity hash |
| `DM_VERITY_FEC=n`, `DM_VERITY_VERIFY_ROOTHASH_SIG=n` | No error correction (a bad block panics, it is not repaired), and the root hash is covered by the UKI's signature, not a kernel keyring |
| `EROFS_FS` | The root |
| `EROFS_FS_ZIP=n`, and so no LZ4, LZMA, DEFLATE or zstd decompressor | The root is uncompressed. Every erofs CVE so far has been in the compressed-data paths, and a decompressor is a parser for attacker-shaped bytes if verity is ever bypassed. The cost is a larger root partition, which the image design budgets. |
| `EROFS_FS_XATTR=n`, `EROFS_FS_POSIX_ACL=n` | The image writes no extended attributes |
| `BLK_DEV_INITRD=n` | No initrd at all; microcode is built in (see [CPU flaws and microcode](#cpu-flaws-and-microcode-t56-t47-s17)) |

What happens when verity finds a bad block is the image design's call. The
table it uses panics, and `panic=10` reboots into the same corrupted root.
ChromeOS avoids that loop by marking the kernel slot bad on verity
corruption so the firmware falls back to the other slot; the A/B layout in
the image design is where that belongs.

**Other filesystems:** ext4 with `QUOTA` and `QFMT_V2` (the data
partition), tmpfs, proc, sysfs, devtmpfs, cgroup2, securityfs. Off: every
other disk filesystem, FUSE, overlayfs, NFS, CIFS, 9p, squashfs, and vfat
(the kernel never mounts the EFI partition). M3 adds, for S16, `DM_CRYPT`
and `DM_INTEGRITY` or authenticated encryption.

**Off for every M0 to M2 target:** USB, Thunderbolt, FireWire, sound, DRM,
framebuffers, HID, wireless, Bluetooth, NVMe and SCSI (no target uses them
yet), KVM host support, and virtio devices nothing uses (balloon, vsock,
9p, input, GPU). Each driver left in is parsing input from a device; T54
says the device may be hostile.

**Adding a driver or filesystem.** A pull request adds the lines to the
target's fragment with the reason, updates the resolved config, and shows
the target booting in CI. A driver goes in a target fragment, not
`common.config`, unless every target needs it. A driver that needs firmware
gets it from Debian's firmware packages ([0007](../decisions/0007-kernel-from-upstream-source.md)).
A driver that is not in mainline, or needs a module, is a reason to reopen
0007.

## Invariants

Each is checked by a test in the [Test plan](#test-plan).

- **K1.** The running kernel is in lockdown confidentiality mode:
  `/sys/kernel/security/lockdown` reads `none integrity [confidentiality]`.
- **K2.** No code can be added to the running kernel: `init_module`,
  `finit_module`, `kexec_load` and `kexec_file_load` return `ENOSYS`, and
  `/proc/modules` does not exist.
- **K3.** System calls for compiled-out features return `ENOSYS` even when
  the caller's seccomp filter allows them: `io_uring_setup`, `bpf`,
  `userfaultfd`, `add_key`, `keyctl`, `msgget`, `shmget`, `semget`,
  `mq_open`, `kcmp`, `modify_ldt`, `iopl`, `ioperm`, `uselib`,
  `name_to_handle_at`; `perf_event_open` too on aarch64. `unshare` and
  `clone3` with a user, network, IPC, UTS or time namespace flag fail with
  `EINVAL`, with a PID or mount namespace flag fail with `EPERM`, and
  `/dev/userfaultfd` does not exist.
- **K4.** The booted kernel's config is the reviewed one: `/proc/config.gz`
  (`IKCONFIG_PROC`, kept for this test; the config is public anyway) equals
  `kernel/resolved/<target>-<arch>.config`.
- **K5.** `/proc/cmdline` contains every word `policy.toml` requires for
  the architecture and no forbidden word. That it equals the image's line
  byte for byte is the image design's check.
- **K6.** Every sysctl in `policy.toml` reads back its value, and after boot
  every write to any file under `/proc/sys` fails with `EROFS`, from every
  process including those holding `CAP_NET_ADMIN`.
- **K7.** No process can ptrace, `process_vm_readv` or open
  `/proc/<pid>/mem` of another; `kernel.yama.ptrace_scope` reads 3. A write
  through `/proc/self/mem` to a read-only page of its own code fails.
- **K8.** The set of active LSMs in `/sys/kernel/security/lsm` is exactly
  {capability, landlock, lockdown, yama}, compared as a set since the order
  changes no outcome, and Landlock reports ABI 6 or later.
- **K9.** After the packet filter is loaded, no service can open a
  `NETLINK_NETFILTER` socket or change the ruleset: the ruleset's hash at
  the end of a run equals the hash right after loading.
- **K10.** No process can create a qdisc, filter or action:
  `RTM_NEWQDISC`, `RTM_NEWTFILTER` and `RTM_NEWACTION` from a process
  holding `CAP_NET_ADMIN` are refused.
- **K11.** Nothing turns a CPU mitigation off: no forbidden word in the
  command line, every `MITIGATION_*` in `policy.toml` set, and on cloud
  targets no file in `/sys/devices/system/cpu/vulnerabilities` starts with
  `Vulnerable` (S17).
- **K12.** KASLR is active: two boots of the same image print different
  kernel offsets, and the console never shows that KASLR was disabled.
- **K13.** The kernel never starts a user program on its own: the static
  usermode helper path is empty, and `/proc/sys/kernel/core_pattern` does
  not exist.
- **K14.** The resolved config passes kernel-hardening-checker with only
  the exceptions in `hardening-exceptions.toml`.
- **K15.** On a machine where the platform lets the guest load microcode
  (bare metal, M5), the revision in `/proc/cpuinfo` is at least the pinned
  package's for that CPU, before PID 1 starts.

## Failure modes

| Failure | What happens | Why that is acceptable |
|---|---|---|
| A point release or a line change alters a default | The resolved config diff fails CI until someone updates it in the pull request | The change is reviewed instead of shipped silently |
| A fragment option does not land (missing dependency, renamed upstream, as `CFI_CLANG` became `CFI`) | CI check 2 fails | A dropped hardening option is found at build time, not by an attacker |
| The tarball's signature or hash does not match | The build fails closed | S4 |
| A service needs something compiled out (wasmtime wanting `userfaultfd`, say) | It fails with `ENOSYS` in the five-minute qemu run of every real service, before merge | The fix is a reviewed config change with a reason, or a change to the service, never a quiet re-enable |
| A kernel bug a service can reach | The service's uid becomes root on that machine (T16) | Accepted in [0001](../decisions/0001-linux-kernel.md). This page shrinks what is reachable; it cannot make what remains bug-free. A compromised machine is replaced (S25). |
| An oops, a UBSAN trap, a CFI violation, a detected list or page table corruption | Panic, panic log on the console, reboot after 10 s | Running on with a corrupted kernel is worse than losing one machine; the cluster tolerates that (T64) |
| A kCFI false positive in a driver's indirect call | The same panic, on every machine that reaches it | The qemu boot and service runs reach the common paths before merge. Permissive mode would turn CFI into a log line, so it stays off. |
| A `WARN` | Logged, the machine keeps running | See `panic_on_warn` above: a cluster-wide reboot on one shared bug is the worse outcome |
| The init's loop hangs | The watchdog resets the machine; `NOWAYOUT` means nothing can disarm it first | The init design's watchdog row |
| aarch64 boots without a KASLR seed | The console says so and the K12 test fails | The target is fixed (virtio-rng, firmware RNG) before it ships |
| A CPU feature is missing (IBT, MTE, pointer authentication) | The kernel boots without it; kCFI still checks indirect calls | Recorded by the boot test so the protection level on each target is known |
| A cloud host lacks a mitigation's microcode | `vulnerabilities` reports `Vulnerable`, the S17 boot test fails, the target does not ship | S17. Accepting it needs a decision record. |
| Kernel security release while a bump is in review | The next release supersedes it; the advisory job opens a new pull request | S9's clock runs from the upstream fix, not from our pull request |
| A hardening option costs Felix more than N1's noise band | N1 fails on the change that introduced it | The default is that security wins; keeping a cost or removing an option needs a decision record with the measurement |

## Security

**Runs as:** the kernel. It reaches everything and is the trusted base for
every other boundary (B2, B3).

**Trusts:** the hardware, firmware and hypervisor
([threat model](../threat-model.md#what-we-trust)); kernel.org's stable
maintainers' keys; upstream commits (T38, accepted); the pinned Clang.

**What an unprivileged service can still reach** after this config and
before its own seccomp filter narrows it: the system call core, VFS,
ext4, erofs reads, tmpfs, procfs and sysfs reads (narrowed by `hidepid` and Landlock),
`AF_UNIX`, UDP and TCP over IPv4 and IPv6, `NETLINK_ROUTE` reads, classic
BPF socket filters, futexes, memory management, signals, pidfds, epoll, and
the cgroup files it owns. That is the residual surface. A holder of
`CAP_NET_ADMIN` adds nf_tables (only the loader) and rtnetlink address and
route changes; a holder of `CAP_NET_RAW` adds packet sockets; a holder of
`CAP_SYS_TIME` adds clock setting.

**Requirements met:** S2 (all of it), S17 (the kernel side; microcode
packaging with the image), the kernel side of S6 (the sandboxing features
exist and the bypasses do not), S7 and S18 (the packet filter exists and
cannot be rewritten after boot), S24 (no core pattern to abuse, read-only
`/proc/sys`, no user namespaces to regain capabilities), S3 (no kernel
interface writes the root), N2. The netfilter/tc rule in S2 or S7, pending.

**Threats answered:**

| Threat | What this page does |
|---|---|
| T16 (root from a service through the kernel) | Shrinks reachable kernel code to the list above; no modules, no namespaces, no BPF, no io_uring, no userfaultfd; memory hardening; lockdown. A bug in what remains still wins. |
| T17, T67 (reading another service or PID 1) | Yama 3, non-dumpable processes, no kallsyms |
| T18, T70 (calling out, rewriting traffic) | nftables only, nfnetlink only for the loader, no tc, no tunnels, tap or xfrm |
| T22 (secrets in crash dumps) | No memory dumps exist; what survives a panic is the log |
| T47 (command line changes) | CI's exact command line; signed from M3 |
| T54 (hostile devices) | No USB or hotplug buses, strict IOMMU where one exists |
| T56 (CPU flaws) | All mitigations, PTI forced, TSX off, early microcode |
| T64 (repeated crashes) | Oops panics and reboots; accepted |

### What the hardening costs

Honest estimates, all to be measured by N1 on the same cloud machines
([#58](https://github.com/GetFelix/ninelives/issues/58)). Debian 13's stock
kernel, the N1 baseline, also runs with CPU mitigations on, so those are not
a NineLives-only cost; the rest are, to the extent the baseline does not
enable them.

| Setting | Where Felix pays | Expectation |
|---|---|---|
| `init_on_free` | Every page evicted from the page cache is zeroed. A broker that streams segments through the cache evicts constantly. | Probably the largest single cost. Upstream's Kconfig help says 3 to 5% typical, up to 8%. |
| `init_on_alloc` | Every page cache page is zeroed before the read that fills it | Upstream says under 1% typical, up to 7% |
| `pti=on` on CPUs that do not need it | Every system call and interrupt switches page tables. QUIC is syscall heavy; GSO and GRO batching (N2) is what keeps the count down. | Small on CPUs with PCID, noticeable on older ones |
| `KSTACK_ERASE` | Every system call | A few percent on syscall-heavy loads |
| CPU mitigations | Every system call and context switch | Large on some CPU generations; shared with the baseline |
| Hardened usercopy, slab hardening, `slab_nomerge`, random kmalloc caches | Copies, allocation; memory overhead for unmerged caches | Small |
| kCFI or FineIBT | Indirect calls in the network and filesystem paths | Small |
| No tc | No `fq` or `fq_codel` | Unknown; measured, see above |
| No io_uring | Felix's optional io_uring flusher is unavailable | Already its own N1 cell |
| Strict IOMMU | DMA unmaps on NVMe and NICs, bare metal only | Measured in M5 |

The perf check: when an N1 cell falls outside the band, a diagnostic run
boots the same image with one setting changed at a time (`init_on_free=0`,
`init_on_alloc=0`, `pti=auto`, and Debian's kernel version with our config)
to name the cause. Diagnostic images are never shipped. What happens next
is a decision record, and by the rule in [requirements](../requirements.md)
the hardening stays unless that record accepts a measured cost and says why.

## Test plan

CI checks run on every pull request that touches `kernel/` or the image
build. qemu tests boot the CI image on both architectures
([#2](https://github.com/GetFelix/ninelives/issues/2)). Tests that read
kernel state use the test image's `nl-inspect` service ([init
design](init.md#test-plan)); tests that try forbidden calls use a test
service whose seccomp filter allows them, so the refusal is the kernel's.
The test image adds a `[[mount]]` for securityfs. The host side of every
qemu test captures the serial console.

| Test | Kind | Proves |
|---|---|---|
| Regenerate the config and compare with the committed resolved config | CI | resolved config matches |
| Every fragment line is in the resolved config as written; every line has a reason; seeded with a misspelt option to see it fail | CI | fragment lines landed |
| `policy.toml` must-set and must-unset lists, seeded with `CONFIG_IO_URING=y` to see it fail | CI | the removed surface stays removed |
| kernel-hardening-checker on config, command line and sysctls | CI | K14 |
| The UKI's command line contains every required word for its architecture and no forbidden word; seeded with one missing and one forbidden word to see it fail | CI | K5 (build side), K11 (command line) |
| Read `/sys/kernel/security/lockdown`; try `/proc/kcore`, `/dev/mem`, `/dev/port`, `/sys/kernel/debug` | qemu | K1 |
| `init_module`, `finit_module`, `kexec_load`, `kexec_file_load`; `/proc/modules` | qemu | K2 |
| Each call in K3 from the permissive test service; `unshare` and `clone3` with each namespace flag | qemu | K3 |
| `/proc/config.gz` against the resolved config | qemu | K4 |
| `/proc/cmdline` against `policy.toml` | qemu | K5 |
| Read every `policy.toml` sysctl; write each from the inspector and from a test service holding `CAP_NET_ADMIN` | qemu | K6 |
| `ptrace(PTRACE_ATTACH)`, `process_vm_readv` and a `/proc/<pid>/mem` write between two test processes of the same uid, and against PID 1; a `/proc/self/mem` write to the caller's own text | qemu | K7 |
| Read `/sys/kernel/security/lsm` and compare as a set; query the Landlock ABI | qemu | K8 |
| From each service's real profile, open `NETLINK_NETFILTER`; hash `nft list ruleset` (test image) after loading and at the end of the run | qemu | K9 |
| From a test service with `CAP_NET_ADMIN`, send `RTM_NEWQDISC`, `RTM_NEWTFILTER`, `RTM_NEWACTION` | qemu | K10 |
| Read `/sys/devices/system/cpu/vulnerabilities/*` | qemu (reported), cloud boot test (fails on `Vulnerable`, M5) | K11, S17 |
| Two test-image boots with a forced panic; compare the printed kernel offsets; search the console for KASLR being disabled | qemu | K12 |
| Read `/sys/kernel/security/`, `/proc/sys/kernel/core_pattern`; crash a test service and check nothing was run and no file was written | qemu | K13 |
| Microcode revision against the pinned package | bare metal (M5) | K15 |
| `UDP_SEGMENT` and `UDP_GRO` on the image's UDP sockets | qemu | N2 |
| Every real service under its real profile for five minutes; fail on any traced call that returned `ENOSYS` or `EINVAL` for an unknown flag | qemu | nothing a service needs is compiled out |
| Felix's own test binaries (`felix-storage`, `felix-broker`, the QUIC tests) on the built kernel | qemu | nothing Felix needs is compiled out, including paths a service run does not reach |
| Mount the data partition with `prjquota`; take and release a `flock` on a directory | qemu | `QFMT_V2`, `FILE_LOCKING` |
| qemu `system_powerdown` on both architectures reaches PID 1 as `KEY_POWER` | qemu | the ACPI power button, with the empty helper path |
| The durability tests from [0001](../decisions/0001-linux-kernel.md) on every line change | qemu, fault | Felix's storage assumptions still hold |
| N1 matrix, and the diagnostic runs above when a cell fails | cloud (M2) | the cost of the hardening is known |

## Open questions

| Question | Proposal | Decides | Needed by |
|---|---|---|---|
| **S11 asks for kernel crash dumps holding memory.** With kexec compiled out that is impossible on the machine. | S11 changes to "the kernel's panic log", kept through pstore (ramoops on qemu, EFI variables where the platform keeps them) and copied to the data partition by the init before the drop. Memory dumps are taken from the hypervisor in test environments only. | S11 design, requirement change | M1 |
| Who reads the kernel log after boot, with `dmesg_restrict` and no `CAP_SYSLOG` anywhere | The init opens `/dev/kmsg` before the drop and hands its output to the S11 sinks like a service's | S11 design, init design | M1 |
| Kernel audit (`CONFIG_AUDIT`) for Landlock and seccomp denials. Landlock ABI 7's denial logging needs it. | Off in M0, so 6.18 is not chosen for that logging. If the S6 profile work needs denial records, audit comes in with that design, in every image, not only test images. | S6 profile design | M1 |
| Host shutdown requests that go through `orderly_poweroff` (Hyper-V `hv_utils`, Xen) do nothing with the empty helper path | Each M5 target on those hypervisors shows how its stop request arrives; if it is not the ACPI button, that target needs a decision (a helper is not the answer) | M5 design | M5 |
| `KSTACK_ERASE` with Clang needs a newer compiler feature than Debian 13's Clang may have | Check the resolved config at the first build; if it does not land, list it as an exception until a pinned toolchain supports it | #3 | M0 |
| Whether felixd asks for speculative store bypass and branch predictor isolation for its WebAssembly workloads | felixd's choice, filed upstream; the kernel leaves the `prctl` controls available | felixd, threat model | M2 |
| `nosmt` on bare metal | Measured and decided with the first bare metal target | M5 design | M5 |
| A queue discipline (`fq`, `fq_codel`) if N1 shows it matters | `NET_SCHED` with that one qdisc and no classifiers or actions, by decision record | Network design | M2 |
| `panic_on_warn` | Off, as above; revisit with WARN counts from the M2 clusters | NineLives maintainers | M2 review of 0001 |
| IPE (run only from the dm-verity root) and `CONFIG_KEYS` for S16's sealed key | Evaluate both with verified boot and encryption at rest | M3 designs ([#9](https://github.com/GetFelix/ninelives/issues/9), [#60](https://github.com/GetFelix/ninelives/issues/60)) | M3 |
| `io.weight` does nothing until iocost is enabled on the device (`io.cost.qos` at the root) | The init enables it when it builds the cgroup tree, or drops `io_weight` from its config | Init design | M1 |
| When to move to the next long-term line | By the rule above: listed as longterm, three months of point releases, and before twelve months from 6.18's end of life | NineLives maintainers | Late 2027 at the latest |
