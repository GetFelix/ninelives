# What NineLives relies on from felixd

felixd is the node agent that runs workloads from the log. NineLives ships it
([0004](decisions/0004-components-from-pinned-releases.md)) and its init
starts it ([0002](decisions/0002-our-own-init.md)). This page lists every
property of felixd the image depends on, as of the version pinned in
[`images/components.toml`](../images/components.toml).

If a felixd release changes anything here, the pull request that bumps it
updates this page too. A property that is not on this page is not one
NineLives may rely on.

Pinned: **felixd 0.1.0**, which needs a **Felix 0.7** control plane and
broker (it is built and tested against Felix 0.7.2).

## The release

| What | Value |
|---|---|
| Architectures | Linux x86_64 and aarch64 (`x86_64-unknown-linux-gnu`, `aarch64-unknown-linux-gnu`) |
| Artifact | `felixd-v<version>-<target>.tar.gz`, holding `felixd-v<version>-<target>/felixd`, `LICENSE` and `README.md` |
| Checksums | `SHA256SUMS` over the tarballs |
| Signature | `SHA256SUMS.sigstore.json`, a keyless Sigstore bundle. Identity `https://github.com/GetFelix/felixd/.github/workflows/release.yml@refs/tags/v<version>`, issuer `https://token.actions.githubusercontent.com` |
| Reproducible | Built twice in felixd's release workflow, the second time on a clean runner, and compared |
| Linking | Dynamically linked against glibc. The 0.1.0 binaries need glibc 2.38 or newer (they reference `GLIBC_2.38` symbols; felixd builds on an Ubuntu 24.04 runner), which Debian 13 (glibc 2.41, [0006](decisions/0006-debian-base.md)) provides. Felix 0.7.2's server has the same floor. |

## Running it

The init starts `felixd agent` as an unprivileged user.
Settings come from environment variables or the matching flags. felixd needs no root
and no capabilities, listens on no port unless metrics are turned on, and
writes nothing to local disk. Workload modules and snapshots live in Felix.

| Setting | Required | What the image sets |
|---|---|---|
| `FELIX_BROKERS` | yes | The brokers from the join config, as `host:port` or `ip:port`, comma separated. Host names are resolved on connect and again when no known broker answers. |
| `FELIX_SERVER_NAME` | no (`localhost`) | The name on the brokers' certificate |
| `FELIX_AUTH_TENANT` | yes | The cluster's tenant |
| `FELIX_NAMESPACE` | no (`default`) | Left at the default |
| `FELIX_AUTH_TOKEN_FILE` | yes | A file on the data partition holding the node token. felixd reads it every time it uses the token, so replacing the file rotates the token. If a later read fails or finds it empty, felixd keeps the last good token. |
| `FELIX_CA_FILE` | no | The cluster CA from the join, on the data partition |
| `FELIX_CONTROLPLANE_URL` | yes | The control plane from the join config |
| `FELIX_CONTROLPLANE_TOKEN_FILE` | no | Not set on the agent. Only `felixd init`, run once on a core machine when the cluster forms, gets the control plane token. With it set, an agent may also run admission. |
| `FELIXD_ADMISSION_TOKEN_FILE` | no | Set only on the core machines that run admission, once the identity design (#16) says how that token is issued |
| `FELIXD_NODE_ID` | no, but the image always sets it | The machine's identity. felixd's default is the host name plus the process id, which changes on every restart. |
| `--metrics-bind` | no (off) | Set only if a later milestone decides to expose metrics. The endpoint has no authentication. |

The image leaves the lease and placement flags (`--lease-ttl-ms` 10000,
`--claim-delay-ms` 1000, `--max-workloads` 64) at felixd's defaults unless a
milestone's design says otherwise.

## Starting

felixd exits with status 1 if it cannot connect to Felix when it starts.
Once connected, it retries its watches every second for as long as it runs.
So the init must restart felixd with backoff when it exits, and must start
it only after the role's Felix services are up on a core or storage machine.

## Stopping

On `SIGTERM` or `SIGINT` felixd stops its workloads, deletes their leases and
deletes its entry in the `nodes` cache, then exits 0. A planned reboot hands
work to other machines at once instead of after a lease TTL.

Each workload gets up to 2 seconds to release, one after another, and the
node entry another 2. The worst case is about `2 s × workloads held + 2 s`,
so with the default `--max-workloads` of 64 about 130 seconds. The init
gives felixd that long before it sends `SIGKILL`. After a `SIGKILL` the
machine's leases are not deleted and other machines wait for them to expire.

## Commands the image and its tests use

| Command | Used for |
|---|---|
| `felixd agent` | The service the init runs |
| `felixd ps` | The status view in M1 and the M2 demo |
| `felixd drain`, `felixd undrain` | Planned shutdown and upgrades (M3) |
| `felixd check --complete` | The M2 pass condition: replays each workload and reports gaps, duplicates and divergence |
| `felixd init` | Run once when a cluster first forms, to set up intake, admission and grants |

## Not in 0.1.0

These are on felixd's plan but not in the pinned release, so NineLives does
not use them yet:

- One failure domain per machine, and drain that hands work back when the
  machine returns ([felixd#82](https://github.com/GetFelix/felixd/issues/82)).
  In 0.1.0 each workload renews its own lease.
- Native programs ([felixd#75](https://github.com/GetFelix/felixd/issues/75)).
  Workloads are WebAssembly.
