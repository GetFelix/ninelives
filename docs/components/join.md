# First-boot join and machine credentials

How a machine with nothing but an image and a join token becomes a member of
one cluster, with its own keys and certificates, and how it gets its Felix
tokens afterwards. This page covers
[#5](https://github.com/GetFelix/ninelives/issues/5) (first-boot join),
[#64](https://github.com/GetFelix/ninelives/issues/64) (S20),
[#73](https://github.com/GetFelix/ninelives/issues/73) (nothing makes node
credentials from a join token) and
[#63](https://github.com/GetFelix/ninelives/issues/63) (S19). Nothing here is
built yet.

[0008](../decisions/0008-ca-keys-and-machine-certificates.md) is binding:
which CAs exist, where their keys live, what the issuer is, the token's
properties, renewal and recovery. This page designs the machine's side of
the join, the config partition, the exchange with the issuer, how the result
lands on disk, and how Felix tokens follow from the certificates. It refines
0008 in three places, all listed under
[Refinements of 0008](#refinements-of-0008): re-delivery after a lost
answer, rate limits without a per-token limit or lockout, and operator
approval of core joins in production.
[0009](../decisions/0009-node-tokens-from-felix-exchange.md) (proposed)
records the one choice this page makes outside 0008: how node tokens are
minted.

The rule behind every choice: the join holds the token and writes every
service's credentials, so it is the machine's whole identity
([threat model](../threat-model.md#what-a-compromised-service-still-reaches),
T71). It takes as little untrusted input as possible, runs once, and fails
closed.

## What it does

`nl-join` is a short-lived Rust program the init runs once, on first boot,
as its own uid with no capabilities
([init design](init.md#programs-it-starts)). It reads the join config from the
raw config partition through one file descriptor the init hands it, waits for
an authenticated clock, connects to the cluster's issuer and checks it against
the issuer root fingerprint in the config before it sends anything, generates
one key per certificate the role needs, redeems the token with proof that it
holds each key, checks what comes back, writes each service's key,
certificate into that service's credential directory, the roots into the
read-only trust directory,
zeroes the config partition, and writes the role last. It then exits for
good. Felix tokens are not part of the join: each service's renewal helper
gets them later by presenting its certificate (see
[Felix tokens](#felix-tokens-after-the-join)).

## What it does not do

- **No issuing.** The join never signs anything for anyone. The issuer
  (`nl-issuer`) signs certificates; it gets its own design, which owns the
  registry, the token store, the admin interface and the ceremony tooling
  ([0008](../decisions/0008-ca-keys-and-machine-certificates.md#consequences)).
  This page fixes only what the issuer must check and return.
- **No renewal.** `nl-renew` renews certificates and fetches Felix tokens
  ([0008](../decisions/0008-ca-keys-and-machine-certificates.md)). This page
  fixes what the join leaves for it.
- **No Felix tokens.** The join never talks to the control plane, never sees
  a Felix token, refresh token, Raft peer token or bootstrap token.
- **No rerun.** A machine that has a role never runs the join again, whatever
  is on its config partition. There is no re-enrollment path through the join
  on a machine that kept its disk
  ([0008: Re-enrollment](../decisions/0008-ca-keys-and-machine-certificates.md)).
  A broken join is fixed by a wipe from outside (S25).
- **No cluster formation.** Creating the cluster tenant, registering the
  issuer as its identity provider, the RBAC grants for machine groups,
  `felixd init`, bootstrap, Raft membership and the Raft peer token belong to
  the formation design. The join only gives a machine the certificates that
  let it take part.
- **No network configuration.** Addresses, routes and DNS are the network
  design's. The join needs a route to the issuer and nothing else; it uses no
  DNS.
- **No TPM in M1.** The kernel has no TPM driver until M3
  ([kernel design](kernel.md)), and Felix reads its keys from files. See
  [Machine keys](#machine-keys).
- **No issuer machines.** Issuer machines are set up at the ceremony, not
  joined. A development image that runs the issuer on its core machine
  starts the issuer before the join; the issuer's design says how.
- **No cloud metadata before M5.** The format and the rules for it are fixed
  here so the M5 work has nothing to decide, but M1 reads the config
  partition only.

## Interfaces

### Overview

```mermaid
sequenceDiagram
    participant Op as Operator
    participant Is as nl-issuer
    participant P1 as PID 1
    participant J as nl-join
    participant T as time client
    Op->>Is: create token (role, node id, expiry)
    Is-->>Op: join config blob
    Op->>Op: write the blob to the machine's config partition
    P1->>P1: before the drop: open the config partition, hand the fd to the join's launcher
    P1->>T: phase 1: start the time client
    P1->>J: phase 2: start the join with fd 3
    J->>J: parse the config, check the clock is synchronized
    J->>Is: TLS, issuer chain checked against the pinned root
    Is-->>J: Hello(issuer time), compared with the local clock
    J->>Is: Join(token)
    Is-->>J: Challenge(nonce, issuer time, kinds the role needs)
    J->>J: generate one key per kind, save them
    J->>Is: Proof(public keys, each signing nonce and TLS exporter)
    Is->>Is: one durable write: token used, node active, incarnation, serials
    Is-->>J: Issued(certificates, roots, cluster values)
    J->>J: check, save the result, write credentials, zero the config, write the role
    J-->>P1: exit 0
    P1->>P1: read the role, start phase 3 (renewal helpers, Felix)
```

### The config partition

Partition 6, `ninelives-config`, 1 MiB, no filesystem
([image design](image.md#partition-layout)). A raw blob keeps the kernel's
filesystem parsers and PID 1 away from input that, on a machine taken from
its owner, is the attacker's (X7, T21).

**Layout.** All integers big-endian.

| Offset | Size | Field | Rule |
|---|---|---|---|
| 0 | 8 | magic | ASCII `NLJOIN01`. Anything else is refused. |
| 8 | 2 | version | `1`. Any other value is refused, not skipped. |
| 10 | 2 | flags | `0`. Any bit set is refused. |
| 12 | 4 | body length `n` | `1..=4096` |
| 16 | `n` | body | Records, below |
| 16+n | 32 | digest | SHA-256 of bytes `0..16+n` |
| 48+n | to 1 MiB | padding | Every byte zero |

The digest is not a signature: whoever can write the partition can recompute
it. It turns a torn or partial write by provisioning, or the wrong disk, into
"corrupt config" instead of a confusing semantic error. The zero padding
means an older, longer config written underneath cannot leave a second token
behind.

**Body.** A sequence of records, each a 2-byte tag, a 2-byte length and that
many bytes. Tags appear in strictly increasing order, except that a
repeatable tag may repeat in consecutive records. Every record's length and
count is checked against the table; a duplicate, a missing required tag, an
unknown tag, a wrong length or a byte left over rejects the whole config.
There is no "ignore unknown": a new field is a new version.

| Tag | Field | Length | Count | Content |
|---|---|---|---|---|
| 1 | token id | 16 | 1 | Random, not secret. Names the token at the issuer and in logs. |
| 2 | token secret | 32 | 1 | 256 random bits. The issuer stores only its SHA-256 (0008). |
| 3 | issuer address | 7..=47 | 1..=4 | `a.b.c.d:port` or `[v6]:port`, ASCII. Literal addresses only: the join does no DNS. |
| 4 | issuer name | 1..=253 | 1 | The DNS name on the issuer's certificate, lowercase LDH labels |
| 5 | issuer root | 32 | 1 | SHA-256 of the issuer root's SubjectPublicKeyInfo |
| 6 | cluster root | 32 | 1 | as above, cluster CA |
| 7 | internal root | 32 | 1 | as above, internal CA |
| 8 | Raft root | 32 | 1 | as above, Raft CA |

The fingerprint is of the public key, not the certificate, as kubeadm's
`discovery-token-ca-cert-hash` is: it pins the key that signs, and nothing
in the certificate's encoding can change it.

The config carries no role, node id, broker list, tenant or control-plane
address. Those come back from the issuer inside the authenticated
connection, bound to the token (see [The exchange](#the-exchange-with-the-issuer)).
What an attacker who rewrites the partition can choose is then only which
issuer the machine trusts, and a machine that trusts the attacker's issuer
joins the attacker's cluster, holding nothing of ours (see
[Failure modes](#failure-modes)).

**Who writes it.** The operator's provisioning tool, from what the issuer's
token command returns, writes all 1 MiB of the partition before first boot:
in qemu onto the disk image, on bare metal through the installer that
writes the image, on a cloud not at all. The token expires in 15 minutes by
default and 60 at most (0008), so the config is written shortly before the
machine first boots. The image ships the partition as zeros, and the image
build checks that it does ([image design](image.md#checks)).

**Cloud metadata (M5).** On a cloud the partition stays zero and the same
blob is delivered as instance user data, base64 without line breaks. The
join fetches it from the provider's metadata service with that provider's
protocol (on AWS, IMDSv2 with a session token), caps the response at 8 KiB,
decodes it and hands the bytes to the same parser. The packet filter lets
only the join's uid reach the metadata address (S18, 0008); after the join
that uid never runs again, so nothing on the machine can read the user data.
The used token stays in the provider's metadata, where it is worthless.

**Which source wins.** A partition that is all zeros is empty. An empty partition on a cloud image means metadata. A
partition that is not empty and does not parse is a permanent failure: the
join never falls back to metadata, so corrupting the partition cannot steer
the machine to another source. A non-cloud image never reads metadata.

### Reading it without root

The block device is root's (`/dev` nodes are 0600), and after the drop
nothing can open it. So PID 1 opens it before the drop, and only on first
boot:

1. In its boot step 4 ([init design](init.md#boot-as-root)), PID 1 already
   finds partitions by GPT name on the root's disk. If `$DATA/join/role` does
   not exist (one `fstatat` that follows no link, beneath the `$DATA` fd;
   `join/` is the join's directory, so the most a planted entry can decide
   is whether this fd is opened), it also finds `ninelives-config` on that
   disk, exactly once.
2. PID 1 opens it `O_RDWR | O_CLOEXEC`, then checks the open fd, not the
   name: `fstat`'s `st_rdev` must equal the partition's sysfs `dev`, and the
   size read from the fd (`BLKGETSIZE64`, before the drop) must be exactly
   1 MiB. From sysfs it also checks that the partition's `start` and `size`
   lie within the GPT's first and last usable LBA and overlap no other
   partition on the disk. GPT entries are written by whoever wrote the disk
   (X7): without these checks, an entry named `ninelives-config` laid over
   the data partition would be read as config and the join's zeroing would
   destroy 1 MiB of data, and one at LBA 0 to 2047 would let the zeroing
   wipe the primary GPT. A failed check, two matches or none: PID 1 closes
   the fd, passes none, and the join fails closed.
3. PID 1 never reads from that fd. It keeps it only until it forks the
   join's launcher, which keeps it through its `close_range`, and PID 1 then
   closes its own copy.
4. The join's launcher passes it to the join as fd 3 and nothing else does,
   and closes it once the join exits 0. On a later boot, when the role
   exists, PID 1 opens nothing.

This is the one exception to the init's rule that a service inherits only
fds 0, 1 and 2 (I11) and that a launcher holds only its listed fds (I20). It
has to be in the init design before code; see
[Changes this design needs elsewhere](#changes-this-design-needs-elsewhere).

The fd is read-write because the join zeroes the partition after a
successful join. The join's seccomp filter allows `pread64` and `pwrite64`
on it, `fdatasync`, and no `ioctl`, so the join cannot reread the partition
table or reach the rest of the disk. A compromised join can write garbage
into the 1 MiB partition, which nothing reads after the join.

The alternatives were worse. Mounting a filesystem puts the kernel's parser
on attacker input as root. `chown`ing the device node to `nl-join` before the
drop gives the join ownership, and an owner can `chmod` it. A copy made by
PID 1 into `$DATA` would have PID 1 reading attacker bytes as root, if only to
copy them.

### The token

| Property | Value | Why |
|---|---|---|
| What it is | A 16-byte id and a 32-byte secret, made by the issuer from `getrandom` | The id lets the issuer find the record and lets logs name a token without revealing it |
| Bound to | One node id and one role, at the issuer | The token cannot be redeemed for any other identity; the machine learns both from the issuer (0008) |
| Lifetime | 15 minutes by default, 60 at most; core tokens default to 60, to leave time for approval | A stolen token is worth something only briefly (0008) |
| Source range | Optional, set when the token is created: the issuer refuses a redemption from outside it. Decided: one issuer check. Useful on a known LAN or VPC, useless behind NAT | A stolen token is also useless from elsewhere on a known network |
| Single use | Marked used in the same durable write that records the node (0008) | A second redemption is refused and alerted |
| Stored | Only `SHA-256(secret)`, at the issuer | A copy of the issuer's registry yields no usable token |
| Travels | Only inside TLS to an issuer the machine has authenticated | A network attacker never sees it (T5) |
| On the machine | In the join's memory, zeroed after use; never written to the data partition | A disk read later yields nothing (S8) |
| After success | The config partition is zeroed | Defence in depth against a bug in the issuer's single-use check |

**Binding to the machine.** In M1 a token is not bound to hardware: whoever
holds it first, before it expires, can redeem it from anywhere that reaches
the issuer. What binds the result to the machine is that the keys are made
on the machine and the issuer checks a proof from each key against a fresh
nonce, so the certificates are useless without keys that never left the
machine. A token redeemed by a thief is caught when the real machine's
redemption is refused (0008). Two further factors:
operator approval for core joins, decided and described
[below](#the-exchange-with-the-issuer), and an optional source address range fixed
at token creation (below). M3 adds hardware binding: the
issuer also checks a TPM attestation that the keys' machine booted a
NineLives image (0008, "Later proofs of identity").

### Machine keys

One ECDSA P-256 key per certificate (0008), generated by the join with
randomness from `getrandom(2)` with flags 0, which blocks until the kernel's
generator is seeded (virtio-rng seeds it in qemu, [kernel design](kernel.md)).
`GRND_INSECURE` and `/dev/urandom` are never used. No key is ever reused for
a second certificate, and the issuer refuses a public key it has seen
before.

**No TPM in M1, stated plainly.** Every key in M1 is a software key in a file
on the data partition, readable by its service and its renewal helper, and,
until M3, stored in plaintext (T52, accepted before M3). That is so even on
hardware that has a TPM, for two reasons. The M1 kernel has no TPM driver;
they come with M3's measured boot ([kernel design](kernel.md)). And Felix and
felixd load their TLS keys from PEM files (`FELIX_TLS_KEY` and the rest), so a
key that cannot leave a TPM could not be used by the service that needs it.
What protects the keys in M1 is file ownership and Landlock (S8, S6), the
24-hour lifetime (S19), and the current-serial check on renewal, which turns
a stolen key into a detected one at the next renewal (0008).

**From M3.** The data partition is encrypted under a TPM-sealed key (S16), so
keys at rest are protected. The machine also gets a TPM-resident attestation
key that never leaves the TPM (the "machine's cluster key" in the
[image design](image.md#rollback)); the issuer then requires it for the join
and for every renewal, so a service key copied off the machine cannot renew
without that machine's TPM. That design is M3's
([#9](https://github.com/GetFelix/ninelives/issues/9)); nothing here has to
change for it except an extra proof in each message.

### Certificates per role

The kinds, CAs and lifetimes are 0008's. The names are fixed by the issuer
from its registry, never taken from the request.

| Kind | CA | Names | EKU | Service | Files in `$DATA/credentials/<service>/` |
|---|---|---|---|---|---|
| felixd client | cluster | URI `felix:principal:<felixd principal>` | client | felixd (every role) | `client.pem`, `client.key` |
| Broker client listener | cluster | DNS: the node id's name; on a storage broker also the cluster's broker name, never on a core broker ([Core machines are internal-only](#core-machines-are-internal-only)) | server | broker (core, storage, core-dev) | `tls.pem`, `tls.key` |
| Broker internal | internal | DNS = node id | server, client | broker | `internal.pem`, `internal.key` |
| Broker control-plane client | cluster | URI `felix:principal:<broker principal>` | client | broker | `cp-client.pem`, `cp-client.key` |
| Control-plane API | cluster | DNS: the control plane's name, the node id's name | server | control plane (core) | `api.pem`, `api.key` |
| Control-plane Raft | Raft | DNS = member id (the node id) | server, client | control plane | `raft.pem`, `raft.key` |

Every certificate also carries the node id and the incarnation, in the
field the issuer's design fixes (0008). Each `.pem` holds the leaf and its
intermediate, so bundles hold roots only (0008).

**Trust.** As 0008 step 5 says, the roots go into one trust directory that
every service can read and none can write: `$DATA/trust/issuer.pem`,
`cluster.pem`, `internal.pem` and `raft.pem`, one root per file, mode 0644,
in a directory owned by `nl-join`, a uid that never runs again. Felix takes
one file per CA setting, so the image config points each setting at its
file, and each service's Landlock ruleset reads only the roots it verifies
peers with: felixd the cluster root, the broker the cluster and internal
roots, the control plane the cluster and Raft roots, the renewal helpers the
issuer root.

Nothing on the machine can change these files after the join, renewal
helpers included. That is deliberate: a renewal helper is network-facing and
runs at its service's uid, and a parser bug there must not be able to widen
what the service trusts. The cost is 0008's own: until a trust update path
is designed
([0008, Consequences](../decisions/0008-ca-keys-and-machine-certificates.md#consequences)),
replacing the cluster, internal or Raft root is a cluster rebuild. A
rolling wipe and rejoin does not work for those three: a machine rejoined
under a new root and one still on the old root reject each other on mutual
TLS ([0008, Recovery](../decisions/0008-ca-keys-and-machine-certificates.md)).
Only the issuer root can roll, by running the old and new issuers side by
side until every machine has been wiped and rejoined under the new one.
Recovering from an issuer compromise needs new roots for every CA that
issuer held, so it is a rebuild. The yearly intermediate replacement does
not touch `$DATA/trust/`, since bundles hold only roots (0008), so this cost
arises only after an issuer compromise or at the roots' 10-year expiry. The [init design](init.md#secrets)
had proposed per-service bundles the renewal helper rewrites; this design
returns to 0008's layout and the init design is updated to match. Decided:
the trust update path is a signed single-purpose updater, built in M3
([#107](https://github.com/GetFelix/ninelives/issues/107)). Until then M1
accepts a rebuild after an issuer compromise.

**Principals.** Each Felix-facing service on a machine is its own Felix
principal, so felixd's credentials do not carry the broker's node grant and
the other way round. The issuer is the identity provider for these
principals ([0009](../decisions/0009-node-tokens-from-felix-exchange.md)).
Its subject for a service is `nl/<node id>/<incarnation>/<service>`, so
every incarnation is a new principal. Revoking one is permanent once the
window under [Renewal and revocation](#renewal-and-revocation) has passed.
Felix names a principal `sha256(iss + "|" + sub)` in hex
([Felix auth](https://github.com/GetFelix/felix/blob/v0.7.2/docs/auth.md#principal-normalization)),
so the issuer computes the principal and writes it into the client
certificate's `felix:principal:` URI, which is what Felix's subject binding
matches
([`serving/tls.rs`](https://github.com/GetFelix/felix/blob/v0.7.2/services/felix-broker-service/src/serving/tls.rs)).

### Core machines are internal-only

Decided ([#77](https://github.com/GetFelix/ninelives/issues/77), T72): core
brokers do not serve tenant clients. Every client token would otherwise be a
way into a core machine's broker, and from there a kernel exploit is the
cluster. What that fixes here:

- A core broker's client-listener certificate carries only its node id's
  name, never the cluster's broker name, so a client sent to a core broker by
  that name fails the TLS check. Only storage brokers carry it.
- The `FELIX_BROKERS` value the issuer returns lists storage brokers only,
  for every role, felixd on core machines included.
- The core role's S7 port list drops the broker client port. Core brokers
  keep their internal port for replication, and the control plane its API
  and Raft ports. A core broker does not set `FELIX_CLIENT_ADVERTISE_ADDR`,
  so client discovery never offers it: Felix offers a broker only when it
  has been told where clients reach it
  ([multi-node client](https://github.com/GetFelix/felix/blob/v0.7.2/docs/multi-node-client.md#discovery)).
- **Core brokers own no tenant streams.** Closing the client port is not
  enough on its own. A request sent to a storage broker for a stream a core
  broker owns is forwarded to the core broker, which parses it and checks
  the token (T73), so any tenant token would still reach a core broker's
  parser over the internal port. So placement keeps every stream and cache
  shard on storage brokers, and core brokers hold none. Felix does this on
  main, after v0.7.2 ([felix#2840](https://github.com/GetFelix/felix/issues/2840), merged as
  [felix#2841](https://github.com/GetFelix/felix/issues/2841)). Core brokers set `FELIX_NODE_ROLE=internal`, which
  registers the label `felix.dev/node-role=internal` in the request that
  creates the node. Placement then never gives it a stream or cache shard,
  at creation, balancing, failover or restoring a lost copy
  ([`plan.rs`](https://github.com/GetFelix/felix/blob/0a50ae1d111eda563532c49de4b9404e21283f9c/services/felix-controlplane-service/src/cluster/placement/plan.rs)),
  and an operator move onto it is refused
  ([`operator.rs`](https://github.com/GetFelix/felix/blob/0a50ae1d111eda563532c49de4b9404e21283f9c/services/felix-controlplane-service/src/cluster/placement/operator.rs)).
  There is no system-stream exception. An unknown role value fails broker
  startup, so a typo cannot leave a core machine open to shards. The role
  is kept once recorded: registering again without it is refused, and
  clearing it needs `node.manage` on `cluster:*`, which a broker's own
  `node:<id>` token does not carry
  ([`nodes.rs`](https://github.com/GetFelix/felix/blob/0a50ae1d111eda563532c49de4b9404e21283f9c/services/felix-controlplane-service/src/api/nodes.rs)).
  Neither does the issuer's group grant, which is scoped to `node:<id>`
  (0009), so an issuer compromise cannot clear it either. Registering
  with the role is refused (409) until every control-plane member runs
  metadata level 44. This holds only with a Felix release that contains it, and the image pins
  v0.8.0-preview.1.
- So a cluster that runs felixd or serves any client needs storage machines
  ([topology.md](../topology.md#cluster-sizes)); the fault-tolerant size is
  [open](#open-questions).

**Join order and the broker list.** Core machines join first, then storage
machines, then workers, as a cluster comes up
([topology.md](../topology.md#bringing-a-cluster-up)). The issuer may create
tokens in any order; the rule is applied once, at redemption (check 10). A
redemption is refused with `retry` if the `FELIX_BROKERS` it would return is
empty, counting active storage brokers plus the redeeming machine itself
when it is a storage machine, so the first storage machine is never refused.
Core joins never wait on storage: a core machine's join needs no broker
list, and its felixd simply cannot connect, and is restarted with backoff,
until a storage broker exists.

`FELIX_BROKERS` is written once, into `$DATA/join/env`, and is only a seed
list. felixd passes it to Felix's client as seeds
([`connection.rs`](https://github.com/GetFelix/felixd/blob/v0.1.0/crates/felixd/src/connection.rs)),
and the client asks the broker it reaches for the current brokers on every
connect and reconnect, never dropping the seeds
([multi-node client](https://github.com/GetFelix/felix/blob/v0.7.2/docs/multi-node-client.md#discovery)).
So brokers added later are learned from the cluster. A machine whose every
seed broker has since been retired cannot connect after a restart; the
issuer lists every active storage broker to make that unlikely, and a
refreshed seed list is the formation design's to add if it proves needed.

**One machine for development.** Decided: development and test images may
run a single machine that is both core and a client-serving broker, so
NineLives can be tried on one machine. It is a separate role, `core-dev`,
fenced by variant (`VARIANT_ID` in `os-release`,
[image design](image.md#building-the-root), S22):

- Only an issuer whose own image is the `development` or `test` variant
  creates a `core-dev` token. A production issuer refuses it, and refuses
  any token whose cluster values would put the cluster's broker name on a
  core machine's certificate or a core broker in `FELIX_BROKERS`.
- Only `development` and `test` images list `core-dev` in their init config.
  A production image does not know the role, so its join refuses an Issued
  answer naming it (a permanent failure, nothing written), and its init would
  start no role service for it ([init design](init.md#programs-it-starts)).
- A `core-dev` machine's broker certificate carries the cluster's broker
  name, `FELIX_BROKERS` lists it, and its S7 port list includes the client
  port. Core approval is skipped, as on every development issuer.

### The exchange with the issuer

One TCP connection to the first issuer address that answers, in the order
listed, with TLS 1.3 only (rustls), no session resumption and no early data,
so nothing is ever sent before a full handshake. The issuer port is fixed in
the image and on S7's list (0008).

**Checking the issuer before sending anything.** The join has no trust store
but the fingerprint. The issuer presents its leaf and the issuer root. The
join takes the root from the presented chain, requires
`SHA-256(SPKI)` to equal config tag 5 and the certificate to be a
self-signed CA, and uses it as the only trust anchor. The leaf must chain to
it, name the config's issuer name, carry the server EKU, and be valid at the
current time, which is why the clock comes first
([Time](#time)). Any failure closes the connection with nothing sent. The
issuer root is its own root and only issuers hold a leaf under it, so a
compromised worker or core machine, which hold leaves under the other roots,
cannot pose as the issuer (0008).

**Messages.** Each message is one frame: a 4-byte length of at most 64 KiB,
then records in the config body's encoding, so one small parser serves both.
Decided: the issuer uses the same encoding.
Unknown tags are refused on both sides.

0. **Hello**, issuer to machine, as soon as the handshake completes: the
   protocol version and the issuer's time. The join compares that time with
   its own clock before it sends the token ([Time](#time)). The issuer runs
   check 3 first: while its own clock is unsynchronized it sends
   Refused(`retry`) instead of Hello, so a bad issuer clock delays joins
   rather than failing them permanently under J14.
1. **Join**, machine to issuer: the protocol version, the token id and the
   secret. The issuer checks the token (checks 1 to 5 below) but commits
   nothing yet.
2. **Challenge**, issuer to machine: a 32-byte nonce, the issuer's time, and
   the kinds the token's role needs. This is how the machine learns what to
   generate without the role ever being in the config.
3. **Proof**, machine to issuer: for each kind, its public key as a
   SubjectPublicKeyInfo and an ECDSA signature by that key over the label
   `ninelives-join-v1`, 32 bytes of TLS exporter output (RFC 8446, label
   `EXPORTER-ninelives-join`, empty context), the nonce, the token id, the kind and the
   public key. The exporter ties the proof to this TLS session, so a proof
   is worthless on any other connection. The join saves the keys before it
   sends this message.
4. **Issued**, issuer to machine: the node id, the role, the incarnation id,
   for each needed kind the leaf and intermediate, the cluster, internal and
   Raft roots, the values for `$DATA/join/env`, and
   the issuer's time. Or **AwaitingApproval**, for a core-role token awaiting
   approval (below), with the key fingerprint. Or **Refused**, with one of
   three codes: `retry` (with a delay), `token_refused`, or `bad_request`.

**Core joins wait for an operator.** Decided: in production, a core-role
redemption is not issued until an operator approves it. A stolen core token
is the cluster (threat model), so the token alone is not enough. The flow:

1. The join's first valid Proof for a core token creates a held redemption
   at the issuer, holding the proven key set. Nothing is committed and
   nothing is signed. The issuer answers AwaitingApproval with the
   fingerprint: SHA-256 over the kinds and public keys in order, shown as
   eight groups of four hex digits.
2. The join computes the same fingerprint itself from its saved keys and
   prints its own value on the console, never the issuer's. If the issuer's
   value differs, the join stops with a permanent failure. It then exits 75
   and is retried with the same saved keys. Each retry proves the keys again
   and gets AwaitingApproval until the operator acts.
3. The operator reads the fingerprint on the machine's console (or the
   hypervisor's serial log) and approves the held redemption at the issuer
   only if the two match. The next retry then gets Issued, after the usual
   durable write.
4. A second, different key set for a token that already has a held one
   blocks the token: neither can be approved, the issuer alerts, and the
   operator issues a new token. So a thief who redeems first cannot win by
   being approved by mistake, and cannot hold the real machine off without
   being seen.
5. Approval and issuance must both happen before the token expires (60 min
   at most), after which the held redemption is dropped and the token is
   refused. Core tokens default to 60 minutes, not 15, to leave time for a
   person to approve.

A held redemption holds no connection and does not count against check 2's
cap on in-flight joins (a Join awaiting its Proof). It is bounded anyway:
at most one per token, since a second blocks the token, and only core
tokens can have one.

Decided: until [felix#2802](https://github.com/GetFelix/felix/issues/2802)
removes the Raft peer token, the operator encrypts it to the new member's
Raft public key, read from the issuer's registry, during core approval. The
issuer relays only the ciphertext and cannot read it, and the join never
holds the token. The control plane's renewal helper fetches the ciphertext
from the issuer.

Development and test images of the issuer skip approval, as they skip other
production rules (0008 names them in the image). Storage and worker tokens
never need it.

`token_refused` covers an unknown token, a wrong secret, an expired, used or
revoked token, and a node id that is not free. The issuer's own log records
which; the wire does not, so a probe learns nothing it could act on.

The signing request is a public key and its kind, not a PKCS#10 CSR. The
issuer takes no name from a request (0008), so a CSR's only use would be
proof of possession, which the nonce signature gives with less ASN.1 for the
issuer to parse.

**What the issuer checks before it signs.** Under 0008 the issuer, not the
Felix control plane, issues machine certificates. Before it signs anything:

1. The connection presented no client certificate. The join message is
   accepted only on such a connection; every other message needs one.
2. Rate limits: failed attempts per source address, and in-flight joins
   (a Join awaiting its Proof) (T65). Over the limit is `retry`. Failures are not limited per token
   id, so someone who saw a token id in a log cannot use it to hold off the
   real machine.
3. Its own clock is synchronized, checked before Hello. If not, `retry`: an issuer with a wrong
   clock would issue certificates the cluster rejects, or accept expired
   tokens.
4. The token id exists; the secret's SHA-256 equals the stored hash
   (constant-time compare); the token has not expired, is not revoked and
   is unused, or the re-delivery rule below applies. A wrong secret raises
   an alert. There is no lockout: 256 random bits cannot be guessed, and a
   lockout would be a way to stop a machine's join.
5. The node id the token names has no active incarnation, unless the
   re-delivery rule below applies to this token, and was never revoked. A
   revoked node id is never reused (S25: a new identity).
6. The keys: every kind the role needs is present, each is a valid P-256
   point, no two are equal, and none matches any key the registry has ever
   seen.
7. Each proof verifies, on this connection, within 30 seconds of the
   challenge.
8. No leaf would outlive its intermediate (0008).
9. For a core-role token on a production issuer, an operator approval of
   exactly the key set just proven, with no second key set pending for the
   token. Otherwise AwaitingApproval, or `token_refused` once the token is blocked.
10. For a storage or worker token, a non-empty `FELIX_BROKERS`, counting the
    redeeming machine when it is a storage machine. Otherwise `retry`
    ([Join order and the broker list](#core-machines-are-internal-only)).

Then one durable write records the token as used, the node as active, the
new random incarnation id, each certificate's serial, and the SHA-256 of
every public key, and only after that write is synced does the issuer sign
and answer (0008 step 4). A refusal at 4 or 5 for a token that exists raises
an alert, as 0008 asks.

**Re-delivery after a lost answer.** This refines 0008 step 4, which refuses
any second use. If the issuer committed and the answer was lost (a dropped
connection, a power cut on the machine), the machine retries with the same
token and the same public keys, which it saved before the first attempt. The
issuer answers that retry with the same certificates, the same incarnation
and serials, after a fresh proof from every recorded key, until the token's
original expiry. It issues nothing new, so the retry is not a second
redemption. A redemption with different keys is refused and alerted, as 0008
says. A thief who has the token but not the machine's saved keys gains
nothing from this rule; one who also has the keys has read the machine's
disk, and the certificates, which are public, add nothing. Without the rule,
an ordinary network drop at the wrong moment costs an operator a new token
and the machine a new incarnation.

**What the join checks in the answer.** Before writing anything:

- the three roots' fingerprints equal config tags 6 to 8, and each is a
  self-signed CA certificate;
- each leaf's public key is the key the join made for that kind, it chains
  through its intermediate to the root 0008 names for that kind, it has
  exactly that kind's EKU, it is valid now, and it lives at most 24 hours
  plus the issuer's 5-minute back-dating;
- the node id and role are well formed and the role is one the image knows;
- every env name is one the image's `join_env` lists name, every value is
  printable ASCII under 1 KiB ([init design](init.md#the-config)), and
  `FELIXD_NODE_ID` equals the node id;
- the issuer's time in Issued is within 60 seconds of the local clock,
  as it was in Hello ([Time](#time)).

Any failure is permanent: an issuer that answers wrongly is not retried.

### Felix tokens after the join

Felix mints tokens only through its control plane: token exchange from a
trusted identity provider, refresh, delegation, and bootstrap
([Felix auth](https://github.com/GetFelix/felix/blob/v0.7.2/docs/auth.md#control-plane-token-exchange-flow)).
It has nothing that takes a join token, and should not: that would put
join-token checking on core machines, which already hold every tenant's
signing keys (T24). So, as recorded in
[0009](../decisions/0009-node-tokens-from-felix-exchange.md):

1. The cluster tenant trusts the issuer as an identity provider, with the
   issuer's assertion-signing public keys configured inline (`jwks`), so the
   control plane fetches nothing and a token with an unknown `kid` is
   refused without a refresh. Felix has this on main, after v0.7.2
   ([felix#2836](https://github.com/GetFelix/felix/issues/2836), merged as [felix#2843](https://github.com/GetFelix/felix/issues/2843);
   [`docs/auth.md`](https://github.com/GetFelix/felix/blob/82a3fe7a6a409bfaf7032cdaf048b01f5c41fe5a/docs/auth.md),
   [`idp_registry.rs`](https://github.com/GetFelix/felix/blob/82a3fe7a6a409bfaf7032cdaf048b01f5c41fe5a/services/felix-controlplane-service/src/auth/idp_registry.rs)).
   The issuer's config sets `jwks` (two keys during a rotation),
   `single_use_jti: true` and `max_token_lifetime_seconds: 60`, the
   assertion lifetime, so Felix refuses an assertion whose `exp - iat` is
   larger; single use needs a bound of at most 600. Changing the keys needs
   `tenant.manage:cluster:*`, and taking a key out revokes every principal
   in the tenant, so retiring the old key at the yearly ceremony ends every
   refresh chain, operators' included, and each service takes a new
   exchange. That is a planned cluster-wide re-exchange: a renewal helper
   whose refresh fails as revoked asks the issuer for a new assertion and
   exchanges again, and the ceremony schedules it. Felix should revoke only
   the principals this issuer vouched for
   ([felix#2850](https://github.com/GetFelix/felix/issues/2850)). There is no
   fallback: it needs a Felix release that contains the change. M1 pins the
   first one that does, expected to be v0.8.0-preview.1, in
   `components.toml` under 0004.
2. Each service's renewal helper, running at the service's uid, asks the
   issuer for an assertion over mutual TLS with the service's current client
   certificate. The issuer answers only for an active node's current
   incarnation and current serial, with an ES256 JWT: `iss` the issuer's
   cluster name, `sub` the service's subject, `aud` the cluster's control
   plane, a groups claim from the registry (the service and, for a broker,
   `node-<node id>`), a `jti`, and a lifetime of exactly 60 seconds (`exp - iat`). Felix accepts
   an upstream token for 60 seconds past its `exp`
   ([`oidc.rs`](https://github.com/GetFelix/felix/blob/v0.7.2/services/felix-controlplane-service/src/auth/oidc.rs)
   sets the leeway that
   [`server/state.rs`](https://github.com/GetFelix/felix/blob/v0.7.2/services/felix-controlplane-service/src/server/state.rs)
   passes), so an assertion is usable for up to 120 seconds. Felix records its `jti` until then and refuses a second exchange of it on the same control-plane replica; the replicas do not share the record ([felix#2844](https://github.com/GetFelix/felix/issues/2844)). A replica also refuses an assertion issued before it started, so one issued just before a control-plane restart fails and the helper asks again. A service needs
   one only when it has no working refresh chain, so the issuer allows a few
   per principal per day and alerts beyond that. The control plane holds no
   Felix principal and asks for none.
3. The helper exchanges it at `POST /v1/tenants/{t}/token/exchange` for the
   service's audience (broker audience for felixd, `felix-controlplane` for
   the broker's node credential) and writes the access and refresh tokens
   into the service's directory. The control-plane API takes a client CA and
   binds the exchange to the principal the assertion resolves to, so the
   helper presents the service's certificate and Felix refuses a certificate
   issued to anyone else ([felix#2821](https://github.com/GetFelix/felix/issues/2821),
   [`server.rs`](https://github.com/GetFelix/felix/blob/77dc0a9803987b775b222462b9bd61fcda1b1933/services/felix-controlplane-service/src/server.rs)).
4. From then on the token is refreshed: by the broker itself from
   `FELIX_NODE_REFRESH_TOKEN_FILE`, which Felix 0.7.2 supports
   ([`config/env.rs`](https://github.com/GetFelix/felix/blob/v0.7.2/services/felix-broker-service/src/config/env.rs));
   by felixd once [felixd#276](https://github.com/GetFelix/felixd/issues/276)
   lands, and until then by felixd's renewal helper. A lost or ended refresh
   chain is replaced by a new exchange, which needs a current certificate.
   Refresh needs one too: Felix refuses a certificate not issued to the
   refresh token's principal, and does not spend the token
   ([felix#2827](https://github.com/GetFelix/felix/issues/2827),
   [`refresh.rs`](https://github.com/GetFelix/felix/blob/0440e0c65d2a11c617f006fa2441bb09151d7552/services/felix-controlplane-service/src/auth/refresh.rs)).

Grants come from groups. Felix maps each group in an assertion to
`group:<iss>#<name>` and evaluates RBAC through it, cluster scope included
([Felix auth](https://github.com/GetFelix/felix/blob/v0.7.2/docs/auth.md#group-based-role-assignment-from-idp-groups_claim)),
so formation grants felixd's node role to `group:<iss>#felixd` once, and the
operator's token command grants `node.manage` over `node:<id>` to
`group:<iss>#node-<id>` when it creates a storage or core token.

The issuer holds no Felix token or admin credential, but its assertion key is
a Felix credential in effect: it can sign any subject with any group of its
own. So an issuer compromise yields every grant made to `group:<iss>#*`:
felixd's node role (the worker row of the
[threat model](../threat-model.md#what-one-compromised-machine-can-do),
which includes cluster-scope reads such as `node.view`), and `node.manage`
over every node. It does not yield tenant administration, RBAC changes,
signing keys, or any grant made to anything other than the issuer's groups.
A compromised issuer can already mint any machine's certificates (0008), so
this adds the Felix half of the same identities and nothing beyond them.
This only holds while groups stay scoped by issuer: the image never sets
`FELIX_CONTROLPLANE_LEGACY_UNSCOPED_GROUPS`, which also links each group
under its bare name and reopens that hole
([`exchange.rs`](https://github.com/GetFelix/felix/blob/v0.7.2/services/felix-controlplane-service/src/auth/exchange.rs)),
and the image config check fails if it is set.

This keeps the join out of the control plane's way, which matters for the
first core machine: at join time its control plane is not running yet, and
the cluster tenant does not exist until formation. The helpers retry until
the exchange works.

`nl-renew`'s design owns the assertion request, the exchange and the
refresh fallback. This page fixes what it relies on: the certificate the
join wrote is the machine's identity, and Felix tokens are derived from it,
never from the join token.

### Files the join writes

All under `$DATA`, through directories the init made
([init design](init.md#boot-as-root) step 5), opened with
`openat2(RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS | RESOLVE_NO_XDEV)`.

| Path | Mode | Content | When |
|---|---|---|---|
| `join/pending/` | 0700, `nl-join` | The join's own crash-recovery state | Created first, removed last |
| `join/pending/keys` | 0600 | The private keys | Before the Proof is sent; removed once the keys are installed |
| `join/pending/issued` | 0600 | The issuer's Issued answer, verbatim | Right after the checks pass |
| `join/failed` | 0640 | The reason for a permanent failure, one line, no secrets | On a permanent failure |
| `credentials/<service>/*.key`, `*.pem` | 0640, group service (setgid directory) | As in the table above | After `issued` is saved |
| `trust/issuer.pem`, `cluster.pem`, `internal.pem`, `raft.pem` | 0644 | One root each; the issuer root is the one the join pinned | With the credentials |
| `join/env` | 0640, group init | `NAME=value` lines from the answer | After the credentials |
| `join/role` | 0640, group init | The role name | Last |

`join/` is setgid with group init ([init design](init.md#boot-as-root)), so
the pending files are 0600: the init can read `env` and `role` but not the
keys. Every file is written to a temporary name, `fsync`ed, renamed into
place, and its directory `fsync`ed. Before `role`, the join calls `syncfs` on
`$DATA` so everything else is on the device first; the init treats a present
`role` as a completed join ([init design](init.md#programs-it-starts)).

**Order of a run:**

1. If `join/failed` exists: exit with the permanent status, contacting
   nothing.
2. If `join/pending/issued` exists: skip to step 9. The config may already
   be zero.
3. If credential files exist with no pending state, the disk holds
   something the join did not write: permanent failure.
4. Read and parse the config (fd 3, or metadata on a cloud image).
5. Wait for the clock ([Time](#time)).
6. Connect, check the issuer, send Join, read the Challenge.
7. If `join/pending/keys` exists and covers exactly the kinds asked for,
   load it; if it exists and does not, permanent failure; otherwise generate
   the keys and save them. Then send the Proof.
8. Check the answer and save it as `join/pending/issued`.
9. Write every credential file, the four root files in `trust/`, and `env`.
   Keys come from `join/pending/keys`; if that is already gone, each
   installed key must match its certificate, or the run fails permanently.
10. `syncfs`, then remove `join/pending/keys`, so no second copy of a key
    outlives the join.
11. Zero the config partition and `fdatasync` it (skipped on a cloud image).
12. `syncfs`, write `role`, remove `join/pending/`, exit 0.

A crash at any step leaves a state that step 1, 2 or 7 resumes from. A crash
between writing `role` and removing `join/pending/` leaves only the saved
answer, which holds certificates and no secret.

**Exit status.** 0 on success. 75 (`EX_TEMPFAIL`) for anything worth
retrying: no route, the issuer down, `retry`, the clock not synchronized, a
full disk. 78 (`EX_CONFIG`) for a permanent failure, after writing
`join/failed`. The init today retries any non-zero exit with backoff
([init design](init.md#failure-modes)); step 1 makes those retries cost
nothing and contact nothing, which is what 0008 asks ("a loud alert, not a
retry loop"). Decided: the init stops on 78. The join is marked `failed` and not restarted.

### Time

Certificates are checked against the clock, so the join's first question is
whether the clock can be trusted, and at first boot it may not be: a board
without a battery-backed clock starts in 1970 or at its build date, and a
cloud image's clock comes from the hypervisor.

S15 requires authenticated time
([#59](https://github.com/GetFelix/ninelives/issues/59)), and its source has
its own chicken-and-egg: NTS key exchange is TLS, and TLS checks certificate
dates. The join does not solve that; the time client does, and the join
waits for it.

- **Where time comes from.** On qemu and clouds with a KVM clock, chrony
  reads the hypervisor's clock (`PTP_1588_CLOCK_KVM`,
  [kernel design](kernel.md)), which needs no network and no certificate. On
  bare metal, chrony uses NTS, and the chicken-and-egg is chrony's own
  `nocerttimecheck` for its first updates. Decided: that first update, and
  only that one, counts as authenticated under S15, with the issuer's clock
  cross-check as the second factor ([#59](https://github.com/GetFelix/ninelives/issues/59)). Either way
  the time client runs in phase 1, before the join.
- **The join waits for a synchronized clock.** It reads the kernel's clock
  state with `adjtimex` (read-only; without `CAP_SYS_TIME` a write is
  refused) and proceeds only when `STA_UNSYNC` is clear and the clock is no
  earlier than the image's build time, which the join carries as a
  constant. Until then it exits 75 and is retried; nothing is sent. On
  Linux, chrony clears `STA_UNSYNC` only when `rtcsync` is set: its
  `set_sync_status` treats the clock as unsynchronized otherwise ("On Linux
  clear the UNSYNC flag only if rtcsync is enabled",
  [`sys_timex.c`](https://github.com/mlichvar/chrony/blob/4.6/sys_timex.c)).
  So the image's chrony config sets `rtcsync`, and the image config check
  requires it. The alternative, asking chronyd over its command socket, is
  out: the init runs chronyd with `cmdport 0`, and a socket the join could
  query is one more input to the join and one more listener on chronyd.
- **A second, independent check.** The issuer sends its time in Hello, as
  soon as the TLS handshake completes and before the join sends the token,
  and again in Issued. If either differs from the local clock by more than
  60 seconds, the join stops with a permanent failure, says why, and sends
  nothing more. This catches a clock moved by a
  compromised time client (T69), which can clear `STA_UNSYNC` as easily as
  set the time. Two clocks, each synchronized from authenticated sources,
  agree far closer than 60 seconds.
- **Certificates tolerate small skew.** The issuer back-dates `notBefore` by
  5 minutes, so a machine a few seconds behind can use its certificates at
  once.

The cross-check cannot help against an attacker who controls both the clock
and a retired issuer's key, since that attacker also chooses the issuer's
time. That needs a time client compromised in the first seconds of a fresh
machine and a key the ceremony deleted.

The join never uses the issuer's time to set the clock, and never checks a
certificate with time checks turned off: an expired issuer leaf from a
retired issuer must not be accepted just because the clock is unknown.

A machine that cannot get authenticated time does not join, and says so in
its output. That is the fail-closed answer, and on clouds whose metadata NTP
servers lack NTS, the hypervisor clock is what makes it work
([init design open question](init.md#open-questions)).

### Renewal and revocation

**Renewal** is 0008's: `nl-renew` per service, at that service's uid, at half
life, with a new key, presenting the current certificate. The join leaves it
everything it needs: the certificate and key in the service's directory, the
pinned issuer root in `$DATA/trust/`, and a
registry at the issuer that records the serials the join received as
current. `nl-renew` starts in phase 3, after the join, so the first
certificate is always there ([init design](init.md#programs-it-starts)).

**Revocation**, for one machine (S19, S25), is three steps an operator tool
runs in order:

1. At the issuer: mark the node revoked and add its current serials to the
   refused list. Renewals and assertions stop at once; certificates die
   within 24 hours (0008).
2. Wait at least 150 seconds: the assertion lifetime (60 s), Felix's leeway
   on upstream tokens (60 s), and 30 s for clock difference between issuer
   and control plane. Under S15 both clocks are set only from authenticated
   sources, which agree far closer than that, and the issuer refuses to
   work while its own clock is unsynchronized (check 3). A control plane
   more than 30 s off is already a T7 problem for every token it mints. Felix's revocation is only a cutoff time. Exchange does
   not compare an assertion with it; it waits until the cutoff second has
   passed and then mints a fresh token and refresh chain
   ([`revocation.rs`](https://github.com/GetFelix/felix/blob/v0.7.2/services/felix-controlplane-service/src/auth/revocation.rs),
   `wait_out_revocation`). Without the wait, a thief who got an assertion
   just before step 1 could exchange it just after step 3 and hold a new
   30-day chain, which a revocation that came before it does not end. On
   Felix main, after v0.7.2, an exchange and a refresh each need a
   certificate issued to the principal ([felix#2821](https://github.com/GetFelix/felix/issues/2821),
   [felix#2827](https://github.com/GetFelix/felix/issues/2827)), so the thief needs the key as well; one who has it
   is who the wait is for. The single-use `jti` does not shorten the wait: it
   stops a second exchange of one assertion on one replica, not the first one
   after step 3. With the
   wait, every assertion issued before step 1 is dead before the cutoff.
   Felix should refuse such assertions itself
   ([felix#2839](https://github.com/GetFelix/felix/issues/2839)); the wait
   stays until it does.
   The Felix steps need an operator certificate from the API's client CA,
   issued to `felix:principal:<operator>`; the operator tooling design owns
   how it is issued (S10).
3. At the control plane: revoke each of the machine's service principals
   (`POST /v1/tenants/{t}/refresh-tokens/revoke`), which ends their refresh
   chains and the broker streams their tokens opened
   ([Felix auth](https://github.com/GetFelix/felix/blob/v0.7.2/docs/auth.md#revoking-a-principal)).
4. At the control plane: remove the `node-<id>` group's grant, so no later
   exchange for that node gets `node.manage`.

What still works after that, and for how long, is listed under
[Failure modes](#failure-modes): the certificate until it expires, and open
connections until Felix bounds them
([felix#2817](https://github.com/GetFelix/felix/issues/2817)).

### Wipe and rejoin (S25)

A compromised machine is wiped from outside, through the hypervisor or a
reimage, never on the machine (S25, [#75](https://github.com/GetFelix/ninelives/issues/75)).
After the revocation above:

- The wipe destroys the data partition, so `$DATA/join/role` is gone and the
  init runs the join on the next boot. A reimage also rewrites the config
  partition as zeros.
- The operator creates a new token. It names a **new node id**, since a
  revoked node id is never reused (issuer check 5), and the join gets a new
  incarnation and new keys. Nothing from the old disk survives.
- A machine that kept its disk never runs the join again, so writing a new
  config onto a running machine's partition does nothing. A machine with a
  half-finished or failed join is wiped too, not repaired.

### Changes this design needs elsewhere

| Where | Change | Status |
|---|---|---|
| [init design](init.md) | Boot step 4 opens `ninelives-config` read-write on first boot only, after checking its size is exactly 2048 sectors and it overlaps no other partition; the join's launcher keeps that fd, passes it as fd 3 and closes it once the join exits 0; I11 and I20 name this one exception; the join's `run_timeout_s` is 120 | Listed as an init open question in this change |
| [init design](init.md) | Trust roots in `$DATA/trust/`, read-only to every service and helper, as 0008 step 5 says; `FELIX_CA_FILE` points there | Updated in this change |
| Time client config | chrony runs with `rtcsync`, so it clears `STA_UNSYNC` ([Time](#time)) | For #59 |
| [topology.md](../topology.md), S7's port list | Core machines are internal-only: the core role has no broker client port, and a cluster that serves clients has storage machines. The cluster size table changes | With the formation design |
| Image config check | Fails if `FELIX_CONTROLPLANE_LEGACY_UNSCOPED_GROUPS` is set; added to S10's never-set list | Requirement updated in this change; check not built |
| [init design](init.md) | Exit 78 from the join means `failed`, no restart | Updated in this change |
| [image design](image.md) | The config partition is written once more on the machine: zeroed by the join after a successful join | Updated in this change |
| [image design](image.md) | The packet filter allows the join's uid TCP to the issuer port and, on cloud images, the metadata address, and nothing else | For the packet filter's ruleset (S18, [#62](https://github.com/GetFelix/ninelives/issues/62)) |
| `nl-issuer` design | Every issuer check and message above; re-delivery; assertions for renewal helpers; inline assertion keys exported for formation | Not written |
| `nl-renew` design | Assertion request, token exchange, felixd refresh until felixd#276; a new exchange when a refresh fails as revoked | Not written |
| Operator tooling design | Who issues operator client certificates (`felix:principal:<operator>`) for the Felix revocation and grant steps and for `felixctl` under the API's client CA | Not written |
| Formation design | Register the issuer with inline keys on the cluster tenant; grant `group:<iss>#felixd`; deliver the Raft peer token to a new core member | Not written |
| [0008](../decisions/0008-ca-keys-and-machine-certificates.md) | Two refinements, [below](#refinements-of-0008) | Stated here; 0008 is not edited |
| Felix | Reload the broker's control-plane client certificate ([felix#2837](https://github.com/GetFelix/felix/issues/2837)); refuse assertions issued before a revocation cutoff ([felix#2839](https://github.com/GetFelix/felix/issues/2839)); replicate the single-use `jti` record ([felix#2844](https://github.com/GetFelix/felix/issues/2844)). Inline keys ([felix#2836](https://github.com/GetFelix/felix/issues/2836)) and the internal role ([felix#2840](https://github.com/GetFelix/felix/issues/2840)) are on Felix main after v0.7.2; M1 needs a release that contains them | Filed, v0.8.0 |

### Refinements of 0008

Decided: all three are accepted. 0008 carries a note pointing here.

| 0008 says | This design | Why |
|---|---|---|
| Step 4: a second use of the token is refused and alerted | A retry with the same, proven keys after a committed but lost answer gets the same certificates; different keys are refused and alerted | A network drop at the wrong moment would otherwise cost a new token and a new incarnation, and the retry issues nothing new ([Re-delivery](#the-exchange-with-the-issuer)) |
| Step 4: attempts are rate limited per source and per token (T65) | Failed attempts are limited per source address and by in-flight joins; there is no per-token limit and no lockout | The secret is 256 random bits, so a per-token limit or lockout adds no protection, and anyone who saw a token id in a log could use either to stop that machine's join |
| Step 4: the issuer signs once the token checks pass | In production, a core-role redemption is also held until an operator approves its key fingerprint against the machine's console | A stolen core token is the cluster; approval makes the token alone insufficient ([Core joins wait for an operator](#the-exchange-with-the-issuer)) |

## Invariants

- **J1.** The join sends no byte of the token until the issuer's leaf has
  been verified against a root whose SPKI SHA-256 equals config tag 5, with
  the issuer name and server EKU checked, at a time the clock is
  synchronized and no earlier than the image's build time, and the issuer's
  time in Hello is within 60 seconds of the local clock.
- **J2.** The token secret is never written to the data partition, output,
  records or any file, and is zeroed in memory after the Join message is
  sent.
- **J3.** Every private key is generated on the machine from `getrandom`
  with flags 0, never leaves the machine, and is used for exactly one
  certificate. The issuer receives public keys and signatures only.
- **J4.** Every certificate the join installs has the public key the join
  generated for its kind, chains to the root 0008 names for that kind, has
  that kind's EKU, is valid at install time and lives at most 24 hours and
  5 minutes. If any certificate fails, nothing is installed.
- **J5.** The roots the join installs are exactly the three in Issued plus
  the pinned issuer root, each matching its config fingerprint, and each
  service's Landlock ruleset reads only the roots listed for that service.
  Nothing writes `$DATA/trust/` after the join.
- **J6.** Each file the join writes is in its service's directory, or in
  `$DATA/join` or `$DATA/trust`, written by temporary file, `fsync` and
  rename. Keys and certificates are mode 0640 with the service's group;
  pending state is 0600.
- **J7.** `$DATA/join/role` is written last, after `syncfs` has returned 0,
  and only when every other output is in place. The init starts no role
  service without it (I24), and never starts the join once it exists.
- **J8.** Values in `$DATA/join/env` come only from the issuer's Issued
  answer, use only names the image's `join_env` lists, and are printable
  ASCII under 1 KiB. Nothing from the config partition reaches `env` or
  `role`.
- **J9.** A config that is not exactly the format above, byte for byte,
  including all-zero padding, is refused whole, and a non-empty partition
  that does not parse is never replaced by another source.
- **J10.** After a successful join on a non-cloud image the config
  partition is all zeros.
- **J11.** After a permanent failure, every later run of the join exits 78
  without opening a socket.
- **J12.** The join holds no capability, opens no listening socket, makes no
  DNS query, and connects only to issuer addresses from the config and, on a
  cloud image, the metadata address.
- **J13.** The issuer signs only after the durable write that marks the
  token used, and a retry under one token yields the same certificates or
  nothing.
- **J14.** The join stops with a permanent failure when the issuer's time
  in Hello or Issued and the local clock differ by more than 60 seconds.
- **J15.** The join receives an fd only for a device whose `st_rdev` matches
  the `ninelives-config` entry, whose size on the fd is exactly 1 MiB, and
  whose range lies within the GPT's usable LBAs and overlaps no other
  partition.
- **J16.** A production issuer issues no core-role certificate without an
  operator approval of exactly the key set proven for that token, and never
  approves a token that has had two different key sets proven.
- **J17.** No core broker certificate carries the cluster's broker name, no
  `FELIX_BROKERS` value the issuer returns lists a core broker, and every
  core broker runs with `FELIX_NODE_ROLE=internal`, so placement gives it no
  shard. This closes T72 once the pinned Felix contains
  [felix#2841](https://github.com/GetFelix/felix/issues/2841).
- **J18.** A `core-dev` role, the only role that puts a client port on a
  core machine, is issued only by a `development` or `test` issuer and
  installed only on a `development` or `test` image.

## Failure modes

Every row fails closed: no credentials are written unless every check
passed, and no role service starts without a role.

| Failure | Detected by | What happens | Why that is acceptable |
|---|---|---|---|
| **Bad token** (unknown, wrong secret, expired, wrong role) | Issuer check 4 | `token_refused`; `join/failed`; exit 78; issuer alerts | An operator issues a new token and wipes the machine |
| **Replay** of a captured exchange | The exchange is inside TLS to the authenticated issuer; proofs bind a fresh nonce and the session's exporter | A replayed Join gets a new nonce the replayer cannot sign; a replayed Proof fails on another session | 0008's x509pop-style nonce plus channel binding |
| **Stolen token, used first by the thief** | The real machine's redemption is refused; the issuer alerts on a second use with different keys | The thief holds that one node id's certificates for that role, at most until the operator revokes the node, plus one certificate lifetime | Bounded by expiry (15 min default), single use, one role. That covers storage and worker tokens. A core token needs operator approval in production, so a thief's redemption shows a fingerprint that does not match the console and is not approved |
| **Stolen token, not yet used** | Not detected | Worthless after expiry | 15 minutes, 60 at most |
| **Stolen config partition before first boot (X7)** | Not detected | As a stolen token, within its expiry | Fingerprints are not secret |
| **Stolen config partition after the join (X7)** | | It is zero | J10 |
| **Rewritten config partition (X7, before first boot)** | Not detected | The machine trusts the attacker's issuer and joins the attacker's cluster. It holds none of ours, and our issuer never hears from it. | An attacker with the disk before M3 can replace the image too (T46). From M3, attestation ties joins to a signed image. |
| **Stolen current certificate and key** (X4 copies them off a running machine) | The real machine's next renewal presents a superseded serial (0008) | Until then the thief can renew, and can get assertions and start refresh chains for that principal. Those chains outlive the certificate, but their tokens need a certificate bound to the principal on every client port, on the control-plane API and on refresh (S10), and on Felix main after v0.7.2 on the forward path too, except that a compromised broker can replay a chain and token it was shown (T73). Revocation (issuer first, a 150 s wait, then Felix) ends all of it | 0008's detected-not-prevented residual; the assertion rate limit and alert make a burst visible |
| **Stolen data partition after the join (X7)** | Not detected | Plaintext keys until M3 (T52): the certificates work until they expire, and a renewal by the thief is detected when the real machine's next renewal presents a superseded serial (0008) | Accepted before M3; S16 closes it |
| **Rogue control plane** | Felix services verify it against the cluster root | The join never talks to it; services refuse it | The join's trust is the issuer root only |
| **Fake issuer: MITM on first contact, a compromised worker or core machine** | J1 | Connection closed before any token byte; exit 75, retried | The issuer root is pinned and only issuers hold leaves under it |
| **A compromised real issuer** | Not by the join | Can issue anything | 0008: the issuer is trusted; recovery is a ceremony |
| **Partial failure: crash before the Proof** | No `issued` | Next run tries again, reusing saved keys if any | The issuer committed nothing |
| **Partial failure: issuer committed, answer lost** | Next run, same keys | Re-delivery returns the same certificates | No new incarnation, nothing new issued |
| **Partial failure: crash while writing credentials** | `join/pending/issued` exists | Next run writes from the saved answer without contacting the issuer | Every write is idempotent |
| **Crash after zeroing the config, before `role`** | `issued` exists, config is zero | Finished from the saved answer | Step 2 runs before the config is read |
| **Power cut while writing a file** | Temporary names | A torn temporary file is overwritten on the next run | Rename is atomic |
| **Clock wrong at first boot** | `STA_UNSYNC`, build-time floor, issuer time check | Not synchronized: exit 75, retried. Synchronized but off by more than 60 s: permanent failure | A machine without trustworthy time must not judge certificates |
| **No authenticated time source at all** | As above | The join never runs its exchange; reported | Fail closed, by S15 |
| **Core join awaiting approval** | AwaitingApproval | Fingerprint on the console; exit 75; retried with the same keys | Production core joins need an operator by design |
| **Two key sets for one core token** (a thief raced the machine) | Issuer, at the second Proof | Token blocked, alert; neither approved; the join keeps getting `token_refused` and fails permanently | The operator issues a new token and wipes the machine |
| **Core token expires before approval** | Issuer | AwaitingApproval redemption dropped; `token_refused`; permanent failure | New token |
| **A `core-dev` token reaches a production machine, or a production issuer is asked for one** | The join's known-role check; the issuer's variant check | The production join fails permanently with nothing written; the production issuer refuses to create the token | The combined role exists only to try NineLives on one machine (J18) |
| **Issuer clock unsynchronized** | Issuer check 3, before Hello | Refused(`retry`) instead of Hello; the join exits 75 and retries | Joins wait for the issuer's clock instead of failing under J14 |
| **Issuer down or unreachable** | Connect timeout | Exit 75; the init retries with backoff | Machines wait; the token may expire, then a new one is needed |
| **Issuer rate-limits** | `retry` | Exit 75 after the stated delay | T65 |
| **Disk full** | Write fails | Exit 75; quota on `$DATA/join` | Retried; nothing half-installed counts, since `role` is last |
| **Config fd missing** (no or two config partitions) | fd 3 not open | Permanent failure | Never guesses which disk |
| **Foreign files in credential directories with no pending state** | Step 3 | Permanent failure | The disk holds something the join did not write; wipe |
| **The join panics or is killed** | Exit status | Treated as 75, retried; pending state resumes | |
| **The join hangs** | `run_timeout_s` | Killed, retried | |
| **A hostile issuer answer** (oversized, malformed, wrong keys or roots) | Frame cap, parser, the answer checks | Permanent failure, nothing written | J4, J5 |
| **A compromised join** (code execution through its parser) | Not detected | It can write any credential and root on this machine (T71), and talk TCP to any host on the issuer port. It holds no Raft peer token and no Felix token. | Its input is a 4 KiB config and the authenticated issuer's frames; it runs once, then its uid never runs again |

## Security

**What it runs as and reaches.** See
[Privileges, sandbox and egress](#privileges-sandbox-and-egress) below. Compromised, it is the
machine's identity (T71) for one boot, and nothing more: no capability, no
other service's uid, no Felix token, no route to anything but the issuer
port.

**What it trusts, and why.**

- The issuer root's key, by its fingerprint in the config. That is the
  join's only anchor. Under X7 the config is attacker input, and the
  consequence is spelled out above.
- The issuer, once authenticated, for everything else: the role, the node id,
  the roots (also pinned), the cluster values. 0008 makes the issuer the
  thing that can mint any machine identity, so the join gains nothing by
  distrusting it, except the cheap sanity checks it makes.
- The time client's synchronized clock, cross-checked against the issuer's.

**Requirements it meets.** S8 (the token never written; keys only in their
service's directory), S10 and S19 (every certificate, per machine, from the
join), S15 (it waits for authenticated time), S18 (egress by port), S20
(single-use, expiring, role-bound token; issuer checked before sending; the
join issues the first certificates), S24 (no capability, a raw partition
read without root), S25 (rejoin under a new identity).

**Threats it answers.** From the [threat model](../threat-model.md):

| Threat | How |
|---|---|
| T2, pose as a broker to felixd | The join installs the cluster root, matched against its fingerprint, as felixd's only CA |
| T5, steal the token or pose as the issuer | Token only inside TLS to the pinned issuer root; single use, short expiry, one role; nonce proofs |
| T6, spoofed DHCP or DNS | The join uses no DNS, and a redirected connection fails J1 before any token byte. Denial of service remains. |
| T8, a parser reached before authentication | The join listens on nothing. The issuer's join endpoint is pre-auth input; it takes only the record encoding with fixed caps, and its hardening is the issuer design's. |
| T7 and T69, a moved clock | Waits for synchronized time; cross-checks the issuer's time |
| T17, read another service's credentials | Each service's files in its own directory with its own group; the join's pending keys 0600 |
| T18, call out from the join | No DNS, TCP to the issuer port only |
| T19, persist through join output | Partial: the join runs once and never reads its output again; the init reads only `role` and `env` with its own checks. Root on the machine can still rewrite them (S25). |
| T21, exploit PID 1 through the join config | PID 1 opens the partition and never reads it; the join parses it as an unprivileged service |
| T22, secrets in a crash dump | The token is in memory only during the exchange and zeroed after; a dump taken then holds it, and dumps are already secrets (S11) |
| T23, secrets in output | J2; output names a token by its id only |
| T30, a stolen machine credential used elsewhere | Client certificates bound to per-service principals; Felix tokens derived from them |
| T31, keep working after revocation | Per-service principals and the four-step revocation; certificates die within 24 h |
| T32, take another machine's node id | Names come from the issuer's registry; a revoked node id is never reused |
| T52, read credentials from a stolen disk | Partial: the config is zeroed and the token never stored; keys are plaintext until S16 (accepted before M3) |
| T65, flood the join | Failed attempts rate limited per source address, and in-flight joins capped; no per-token limit and no lockout, a declared [refinement of 0008](#refinements-of-0008) |
| T71, a compromised join | Runs once; no capability; installs only roots matching the config's fingerprints; no Raft peer token |
| T72, attack a core broker with client tokens | Closed once the pinned Felix contains [felix#2841](https://github.com/GetFelix/felix/issues/2841): no cluster broker name on core certificates, no core broker in `FELIX_BROKERS` or discovery, no client port in the core S7 list, and `FELIX_NODE_ROLE=internal` on every core broker, so placement gives it no shard and forwarding no longer reaches it (T73). Narrowed until then |
| X7 | Config zeroed after use; a rewritten config cannot reach our cluster |

T4, T24 and T25 are not changed by the join: they are Felix's
([felix#2802](https://github.com/GetFelix/felix/issues/2802),
[felix#2801](https://github.com/GetFelix/felix/issues/2801)). T10 (bootstrap)
and T28 (node trims) belong to the formation design.

**Dependencies on Felix and felixd.** Still open:

| Gap | Effect here | Issue |
|---|---|---|
| The single-use `jti` record is in memory in each control-plane replica | An assertion replayed on a replica that has not seen it is exchanged once more there, inside its 120 s window: at most one chain per replica, N-1 beyond the legitimate one: 2 extra on the 3-core cluster. The exchange also needs a certificate issued to the principal (S10), so the thief needs the machine's key as well | [felix#2844](https://github.com/GetFelix/felix/issues/2844) |
| Exchange sleeps past a revocation cutoff instead of refusing older assertions | The operator tool waits 150 s between the issuer and Felix revocation steps | [felix#2839](https://github.com/GetFelix/felix/issues/2839) |
| The broker reads its control-plane client certificate once | A 24-hour certificate expires under a running broker | [felix#2837](https://github.com/GetFelix/felix/issues/2837) |
| Connections outlive their certificate | A revoked machine's open connection outlives its certificate | [felix#2817](https://github.com/GetFelix/felix/issues/2817) |
| Raft TLS read once | Control-plane members restart after Raft renewal (0008) | [felix#2815](https://github.com/GetFelix/felix/issues/2815) |
| Internal port serves before an identity-checked Hello | Any internal certificate acts as any broker on replication | [felix#2819](https://github.com/GetFelix/felix/issues/2819) |
| A forwarded request has no proof that the client holds its certificate key | A compromised broker can replay a chain and token it was shown, until either expires (T73) | [felix#2818](https://github.com/GetFelix/felix/issues/2818) |
| felixd presents no client certificate | felixd's tokens are bearer tokens | [felixd#275](https://github.com/GetFelix/felixd/issues/275) |
| felixd does not refresh its token | felixd's renewal helper refreshes it until then | [felixd#276](https://github.com/GetFelix/felixd/issues/276) |

Closed on Felix main, on main after v0.7.2, in no 0.7 release:

| Change | What it closes here |
|---|---|
| Inline identity-provider keys, single-use `jti` and a lifetime bound ([felix#2836](https://github.com/GetFelix/felix/issues/2836), merged as [felix#2843](https://github.com/GetFelix/felix/issues/2843)) | The issuer is trusted with no JWKS fetch. A second exchange of one assertion on one replica is refused |
| `FELIX_NODE_ROLE=internal` keeps every shard off a broker ([felix#2840](https://github.com/GetFelix/felix/issues/2840), merged as [felix#2841](https://github.com/GetFelix/felix/issues/2841)) | Core brokers hold no shard (J17, T72) |
| Refresh bound to the client certificate ([felix#2822](https://github.com/GetFelix/felix/issues/2822), merged as [felix#2827](https://github.com/GetFelix/felix/issues/2827)) | A stolen refresh token needs the principal's certificate |
| The owner checks the certificate binding on forwarded requests ([felix#2812](https://github.com/GetFelix/felix/issues/2812), merged as [felix#2823](https://github.com/GetFelix/felix/issues/2823)) | Forwarded requests no longer skip the binding (T73, narrowed) |
| The control-plane API takes a client CA ([felix#2811](https://github.com/GetFelix/felix/issues/2811), merged as [felix#2821](https://github.com/GetFelix/felix/issues/2821)) | S10's mutual TLS holds on the API |
| The server's `SHA256SUMS` is signed with Sigstore ([felix#2796](https://github.com/GetFelix/felix/issues/2796), merged as [felix#2845](https://github.com/GetFelix/felix/issues/2845)) | The pinned Felix can be verified like felixd (0004) |

None of these is in a release yet. M1 pins the first Felix release that
contains them, expected to be v0.8.0-preview.1, in `components.toml` under
[0004](../decisions/0004-components-from-pinned-releases.md). A tagged
preview is a release; a nightly is not, and 0004 does not allow pinning one.
J17 and the T72 row close when that pin lands.

### Privileges, sandbox and egress

| | Value | Why |
|---|---|---|
| uid | `nl-join`, its own | T71: the uid runs once, so nothing later shares it |
| Capabilities | none | Everything it writes is in directories the init made for it |
| fds | 0, 1, 2, and fd 3, the config partition, on first boot only | [Reading it without root](#reading-it-without-root) |
| Landlock read | `/usr/lib/ninelives/nl-join`, the libraries it links, `/proc/self`, `$DATA/join`, `$DATA/trust`, each `$DATA/credentials/<service>` | Its own outputs, to resume and to check installed keys. No system trust store, no `/etc`, no DNS config |
| Landlock write | `$DATA/join`, `$DATA/trust`, each `$DATA/credentials/<service>` | Exactly its outputs. Every file call is confined here by Landlock, not by seccomp, which cannot read paths |
| Landlock devices | `/dev/null` | Randomness comes from `getrandom`, not a device node |
| Network | TCP connect, IPv4 and IPv6 | To the issuer; on a cloud image also to the metadata address |
| seccomp | An allowlist: the file calls it uses (`openat2`, `read`, `write`, `fsync`, `renameat2`, `unlinkat`, `mkdirat`, `syncfs`), `pread64`, `pwrite64` and `fdatasync` on fd 3, `getrandom`, `adjtimex`, `clock_gettime`, `socket` for `AF_INET` and `AF_INET6` with `SOCK_STREAM` only, `connect`, `sendto` and `recvfrom` on that socket, memory and thread calls, `exit_group`. No `ioctl`, no `execve`, no `AF_NETLINK`, no `AF_UNIX`, no UDP | No DNS, no other process, nothing beyond one TCP connection |
| Egress (S18) | The join's uid may connect only to the issuer's TCP port, and on a cloud image to the metadata address | The ruleset is static, from the image, and the issuer's address is in the config, which only the join parses. Decided: egress is limited by port, not by address. |
| Ingress | none | The join listens on nothing |
| Run timeout | 120 seconds per run | A hung connection is a transient failure, retried |
| cgroup | 64 MiB memory, small pids limit | It parses at most 4 KiB of config and a 64 KiB frame |

The S7 list's "the join" means the issuer's join endpoint on an issuer
machine. An ordinary machine opens no port for the join.

## Test plan

Unit tests run the join's state machine with a fake issuer, fake clock and a
fake partition file. qemu tests boot the image with a test issuer on the test
network. Fault tests use a test-image hook that kills the join at a named
step.

| Test | Where | Proves |
|---|---|---|
| Config parser fuzzed (cargo-fuzz) and property-tested: every single-byte change, truncation, extension, reordered or duplicated tag, nonzero padding, wrong version or flag is refused; the canonical encoding round-trips | unit, CI and scheduled fuzzing | J9 |
| Frame parser fuzzed the same way, against oversized and malformed Issued answers | unit, scheduled | Hostile answer row |
| A test issuer with a leaf under another root, a wrong name, an expired leaf, a server-EKU-less leaf: the issuer's capture shows zero application bytes | qemu | J1, S20 (wrong CA) |
| Used, expired, unknown and wrong-role tokens: `token_refused`, `join/failed`, a second boot exits 78 without a connection (packet capture) | qemu | S20, J11 |
| Join twice with one token from two machines: the second is refused and the issuer raises an alert | qemu | single use, stolen token row |
| Drop the connection after the issuer's commit: the next run gets the same certificates and incarnation | unit and qemu | J13, re-delivery |
| Kill the join at each step 6 to 12, then boot again: it finishes, or fails permanently where the table says, and never yields two incarnations | fault | partial failure rows, J7 |
| Answers with a certificate on another key, under the wrong root, with a 48-hour lifetime, a root that does not match its fingerprint, an env name outside `join_env`: nothing is written | unit | J4, J5, J8 |
| Clock: start with `STA_UNSYNC` set; with a clock before the build time; with an issuer 2 minutes off. No token sent in any of them; permanent failure in the third | unit and qemu | J1, J14 |
| Core join on a production test issuer: the console shows the fingerprint, retries get AwaitingApproval, approval leads to Issued; a second key set for the same token blocks it and alerts; expiry before approval drops it. On a development issuer the core join is issued at once | unit and qemu | J16 |
| A core machine's broker certificate has no cluster broker name; its broker runs with `FELIX_NODE_ROLE=internal`; `FELIX_BROKERS` on every role lists only storage brokers; from a worker, the core broker client port is closed (S7 scan); a token for a cluster with no storage machine is refused | unit and qemu | J17, #77 |
| The first storage token redeems with no storage machine active; a worker token redeemed before any storage machine gets `retry` and then succeeds once one is active; a core join never waits on storage | unit | check 10 |
| felixd on a worker started with one seed broker reaches a storage broker added later, and is never offered a core broker | qemu | Seed list, discovery |
| A storage broker forwards no tenant request to a core broker: with `FELIX_NODE_ROLE=internal`, no tenant shard is placed on a core broker, including after storage machines fail | qemu, once the pinned Felix contains felix#2841 | T72 |
| An AwaitingApproval whose fingerprint differs from the join's own makes the join fail permanently; the console shows the join's own value | unit | J16 |
| A production issuer refuses to create a `core-dev` token; a `core-dev` Issued answer from a test issuer makes a production image's join fail permanently with nothing written; on a development image the same answer gives a working one-machine cluster whose broker serves clients | unit and qemu | J18 |
| A test issuer whose clock is unsynchronized and 5 minutes off: it answers Refused(`retry`) instead of Hello, the join exits 75 with no token sent and no `join/failed`, and it joins once the issuer's clock is synchronized | unit and qemu | Issuer clock row, J11 |
| With the image's real chrony and the KVM clock in qemu, the join gets past the clock wait within 60 s of boot; with `rtcsync` removed from a test config, it never does and says why | qemu | Time, `rtcsync` |
| Disks with a `ninelives-config` entry of the wrong size, one overlapping the data partition, one at LBA 0 to 2047, and two such entries: no fd reaches the join, it fails permanently, and the data partition and the primary GPT are unchanged | qemu (test images) | J15 |
| Revocation tool: an assertion obtained just before the issuer step, exchanged after the Felix step, is refused or its chain is ended | qemu, M2 | Renewal and revocation |
| After a join: the config partition reads all zeros; file modes and groups as in the table; `role` is newer than every other file | qemu | J6, J7, J10 |
| A planted known token and test keys: the console, captured output, records and the data partition outside the credential directories are searched; the token is never found anywhere | qemu | J2, S8 |
| Under the join's profile: opening `/dev/vda`, an `ioctl` on fd 3, a UDP socket, a connection to another port, writing outside its directories all fail | qemu (test image) | J12, sandbox |
| A second boot does not start the join; a new config written onto a joined machine's partition is ignored | qemu | No rerun, S25 |
| S20's bootstrap check stays with formation | formation's tests | |
| Revoke one of three machines: within one certificate lifetime it cannot read or write, the other two keep working | qemu, M2 | S19, with [#63](https://github.com/GetFelix/ninelives/issues/63) |
| Wipe and rejoin one of three: new node id, new incarnation, old credentials refused everywhere | qemu, M2 | S25, with [#75](https://github.com/GetFelix/ninelives/issues/75) |

## Open questions

None left. Decided and moved into the design: core machines are
internal-only ([#77](https://github.com/GetFelix/ninelives/issues/77)), core
joins wait for an operator, the [refinements of 0008](#refinements-of-0008),
and the root updater
([#107](https://github.com/GetFelix/ninelives/issues/107), M3). Also decided:

- the [Raft peer token](#the-exchange-with-the-issuer) reaches a new core
  member encrypted to its Raft public key, until
  [felix#2802](https://github.com/GetFelix/felix/issues/2802);
- the init [stops on exit 78](#files-the-join-writes);
- egress is [limited by port](#privileges-sandbox-and-egress), not address;
- a token may [name a source address range](#the-token);
- the [fault-tolerant production size](../topology.md#cluster-sizes) is 3 core + 2 storage;
- the issuer [uses the config's record encoding](#the-exchange-with-the-issuer);
- on bare metal, the [first chrony update](#time) counts as authenticated time.
