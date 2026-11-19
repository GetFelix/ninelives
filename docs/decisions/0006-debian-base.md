# 0006: The root is built from a minimal, pinned Debian 13

**Status:** Accepted, 2026-11-18. Holds through M2
([#1](https://github.com/GetFelix/ninelives/issues/1)).

## Context

Everything in the root that NineLives does not write, and does not pin in
[`images/components.toml`](../../images/components.toml), comes from a base
distribution: glibc, CA certificates, the DHCP and time-sync clients the
init starts, and a few libraries.

A NineLives machine upgrades by swapping its whole image, so it has no
package manager at run time. The distribution's packages are inputs to the
image build. That makes these the properties that matter, in order:

1. Security fixes that arrive quickly and can be acted on automatically.
2. Exact package versions that can be fetched again years later, so an
   image can be rebuilt and checked byte for byte.
3. glibc 2.38 or newer, which Felix's and felixd's release binaries need.
4. x86_64 and aarch64 now, a Raspberry Pi in M5.
5. Good mkosi support without bringing systemd in as PID 1
   ([0002](0002-our-own-init.md), [0003](0003-mkosi-image-build.md)).
6. Source for every package archived next to it, for the GPL obligations
   of publishing images.

The kernel is not on this list. It is built from upstream source
([0007](0007-kernel-from-upstream-source.md)), so the distribution's kernel
does not matter.

## Decision

The root is built from Debian 13, installing only the packages the image
needs, each named in the image configuration. Packages are fetched from
[snapshot.debian.org](https://snapshot.debian.org/) at a pinned timestamp,
so a build of a given commit always gets the same versions. Moving the
timestamp is a reviewed pull request, like bumping a component.

A Debian security advisory for any package in the image triggers a pull
request that moves the timestamp, and the image is rebuilt within the
deadline in [requirements.md](../requirements.md#security).

The image's inventory lists every package with its version and source
package, next to the components from `components.toml`.

## Options considered

| Option | Why not |
|---|---|
| Fedora CoreOS | The strongest security posture out of the box (SELinux enforcing, automatic updates, signed images), and much of M1, M3 and M5 exists in it. But it is a whole OS: systemd is PID 1 and images are built with rpm-ostree or bootc, so it reverses 0002 and 0003, and NineLives becomes a derivative following its update streams. The parts it gives us are the ones NineLives exists to own. |
| Ubuntu 24.04 LTS | Qualifies (glibc 2.39, long support, an archive snapshot service), but adds one company's choices on top of Debian, and its snapshot service is younger. Its cloud reach does not help: M5 builds its own cloud images. |
| Fedora | The strongest default compiler hardening and current packages, but each release lasts about 13 months, so the base moves every year. |
| CentOS Stream, AlmaLinux, Rocky 10 | Long support, but older and smaller archives. Stream is a moving preview, and the rebuilds' snapshot archives are weaker. Their SELinux policy assumes systemd. |
| Wolfi | Small, glibc, and patched quickly, but rolling, built for containers with no boot story, and young. Worth watching. |
| Alpine | musl, so Felix and felixd would need musl builds. |
| Nix | Reproducible by construction, but replaces mkosi and our build model instead of feeding it. |
| No distribution (Buildroot, Yocto, our own) | The smallest surface, but every package's security fixes become ours to track, and one missed fix is a hole. |

## Consequences

- The image carries about a dozen Debian packages, not a Debian system:
  no package manager, shell or SSH server in production images
  ([requirements.md](../requirements.md#security)).
- Debian's default compiler hardening is a step behind Fedora's. The
  packages we take are few and small, and the code that does the work
  (the init, Felix, felixd) is Rust that we or our upstreams build.
- Security support for Debian 13 runs to mid-2028, and Debian LTS extends
  it to mid-2030. Moving to Debian 14 is a planned, reviewed change.
- Debian's reproducible-builds work and snapshot archive make the
  reproducibility check in [0003](0003-mkosi-image-build.md) achievable.

## Reopen when

- A package we need is missing, or too old to meet a requirement, and a
  backport would not fix it.
- Debian's security response falls behind the deadline in the requirements.
- The M2 review ([#53](https://github.com/GetFelix/ninelives/issues/53)).
