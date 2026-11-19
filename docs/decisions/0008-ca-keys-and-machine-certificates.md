# 0008: CA keys live offline and on issuer machines; machines renew their own certificates

**Status:** Proposed, 2026-11-18. Answers
[#16](https://github.com/GetFelix/ninelives/issues/16) for M1.

## Context

Mutual TLS is the baseline on every Felix listener (S10), and the broker
binds a client certificate to the subject of the token it carries. Felix has
no certificate revocation, so certificates live for 24 hours at most and are
renewed at half life (S19). A compromised machine is wiped and rejoins under
a new identity (S25). Nothing runs as root after early boot (S24).

M1 needs three things this record decides: which CAs exist and where their
keys live (A3 in the [threat model](../threat-model.md) says "not decided"),
how a joining machine gets its first certificates (S20, T5, T65, T71), and
how certificates are renewed every 12 hours without root.

The constraint that shapes everything: a core machine is already the whole
cluster today, because it holds every tenant's signing-key seeds and the
shared Raft peer token (T24, T25,
[felix#2801](https://github.com/GetFelix/felix/issues/2801),
[felix#2802](https://github.com/GetFelix/felix/issues/2802)). Those are
Felix's to fix. This record must not add a third reason, and must leave a
core compromise recoverable once Felix fixes the other two.

What Felix 0.7.2 does with certificate files, from its code:

- Broker client, broker internal and control-plane API certificates and keys
  are re-read every 30 seconds. A cert and key that do not match are
  refused and the old pair kept, so a renewal written over the same paths is
  picked up without a restart.
- The control-plane Raft listener and its peer client read their
  certificate, key and CA once, at startup. So does the bootstrap listener.
- Every CA bundle is read once, at startup.
- The Raft listener checks only that a peer certificate chains to the
  configured CA (T4). Broker replication checks the certificate's DNS name
  against the node id. The client listener, with subject binding on, wants a
  `felix:principal:<sub>` URI SAN.

## Decision

**Three CAs for machines, each with its own root, and a fourth root for the
issuer.** Every root is created offline, in a ceremony, and its key never
touches a networked machine. Each machine root signs one intermediate per
issuer machine. The intermediates live on **issuer machines**: a fourth role
that runs the NineLives issuer service and a time client, and no Felix, no
felixd and no workloads. Development and single-machine clusters may run the
issuer on their core machine, and the image says so in its name, like a
development image does for the shell.

An issuer machine holds keys that can mint any machine's identity, so it is
worth more than a core machine and runs less. Its network configuration is
static, with no DHCP client. It runs no diagnostics service. It listens on
one port, the issuance endpoint, and requires a client certificate for every
request except a join, which has none yet and is authenticated by its token.

| CA | Issues | Trusted by | Why its own root |
|---|---|---|---|
| Cluster CA | Broker client-listener certificates (the cluster's broker name and the machine's own), control-plane API certificates, client certificates for felixd and other machine principals (`felix:principal:<sub>`) | felixd, brokers' client listeners (`FELIX_TLS_CLIENT_CA`), brokers as control-plane clients (`FELIX_CONTROLPLANE_CA`), joining machines | Issued to every machine, workers included |
| Internal CA | Broker replication certificates, DNS name = node id | Brokers' internal port (`FELIX_INTERNAL_TLS_CA`) | Core and storage machines only. A worker's certificate must not chain to it. |
| Raft CA | Control-plane Raft certificates, DNS name = member id | Raft listeners and peer clients (`FELIX_RAFT_TLS_CA`) | Core machines only. While the Raft listener checks only the chain (T4), any certificate under this root can speak for any member. |
| Issuer CA | The issuer's server certificate, one per issuer, signed by the root at the ceremony | Joining machines and `nl-renew` | Only issuers hold a leaf under it, so it alone can say "this is the issuer" |

They are separate roots, not three intermediates under one, because Felix
trusts whatever chains to the bundle it is given: with a shared root, a
worker's client certificate would chain on the Raft listener.

The issuer gets its own root for the same reason. Every machine holds a leaf
under the cluster root, so a joining machine that trusted any certificate
chaining there would hand its token to a compromised worker posing as the
issuer. A check on the name and extended key usage would rest entirely on
the issuer never signing those for anyone else; a separate root makes it a
question of which key signed, not of policy. Pinning the issuer leaf's
fingerprint in the join token would also work, but that leaf differs per
issuer and changes at every ceremony, so tokens made before a ceremony would
stop working. The issuer root has no online intermediate, so even a
compromised issuer cannot mint a second issuer.

The image signing key (S13) is out of scope. It signs what every machine
boots, belongs to the build pipeline (A6), and is never on a cluster
machine.

**Lifetimes.** Roots: 10 years. Intermediates and issuer certificates: one
year, renewed at a yearly ceremony. Machine certificates: 24 hours, renewed
at 12 hours with up to an hour of jitter. Keys are ECDSA P-256, which every
TLS stack on the path verifies. A leaf file holds the leaf and its
intermediate, so trust bundles hold only roots and do not change when an
intermediate does.

The ceremony is yearly so that one missed date is not a dead cluster. The
issuer alerts daily from 90 days before an intermediate expires, and the
ceremony is due by 60 days before. The issuer never signs a leaf that would
outlive its intermediate: in the last 24 hours it cuts each leaf to end with
the intermediate, so leaves get shorter and renew more often. Once the
intermediate expires the issuer signs nothing under it, and machines stop as
their last leaves expire, within 24 hours.

**The issuer** is a small Rust service, `nl-issuer`, running as its own
user with no capabilities. It listens on one TLS port (added to S7's list)
and holds the intermediates' keys, a registry of node ids with their role,
status and current certificate serials, and the hashes of unused join
tokens, all on its data partition. It signs a certificate only when the
names in the request are exactly the names the node's role gets: it never
takes a name from the request on trust.

**Joining** (M1 ships the join token; see below for the rest):

1. An operator creates a join token at the issuer. The token names one role
   and one new node id, carries 256 random bits, expires in 1 hour by
   default (24 hours at most), and is stored only as a hash.
2. The join config holds the token, the issuer's address, and the SHA-256
   fingerprints of the four roots. The operator writes it to the machine's
   config partition, or the cloud delivers it as instance metadata. When it
   comes from metadata, the packet filter blocks the metadata address for
   every uid except the join's, so no other service can read the token.
3. On first boot the join service connects to the issuer and checks its
   certificate against the issuer root's fingerprint before it sends
   anything (S20, T5). It generates one key per certificate the role needs,
   and sends the token with a signing request for each.
4. The issuer marks the token used in the same write that records the node
   as active and its new serials, then signs. A second use of the token is refused and raises an
   alert: either the token was stolen or the machine is retrying after a
   crash, and an operator decides which. Attempts are rate limited per
   source and per token (T65).
5. The join writes each key and certificate into that service's credential
   directory, and the four root bundles into a trust directory that every
   service can read and none can write. It deletes the token from the join
   config, then exits for good (T71).

What stops a replay: the token travels only inside TLS to an issuer the
machine has authenticated, works once, expires in 1 hour by default and 24
hours at most, and yields
only one node id's certificates for one role. A thief who uses it first gets
that one machine's identity, and the real machine's failure to join shows
it.

**Renewal** is done by `nl-renew`, which the init starts once for each
service that holds a certificate (broker, control plane, felixd), as that
service's own uid, with the same Landlock view of that service's
directory. It holds nothing the service does not already hold. At half life
it generates a new key, connects to the issuer with the current
certificate, and asks for the same names.

The issuer renews only an unexpired certificate of an active node, and only
if its serial is the one the registry records as current for that node and
that kind of certificate. A superseded serial is refused and raises an
alert, so a thief who renews a stolen certificate first makes the real
machine's next renewal trip it, and the other way round. The new
certificate comes from the same CA, for the same kind and names, as the one
presented: felixd's `nl-renew` holds a cluster certificate and cannot come
away with a Raft one.

`nl-renew` writes the key and certificate to temporary files in the
service's directory, syncs them and renames them over the old ones. Felix's
30-second reload picks them up, and keeps the old pair if it reads the two
files between renames. No key or certificate passes through PID 1, and no
process writes into another service's directory after the join. `nl-renew`
cannot change the trust directory, so renewal can never widen what a
service trusts.

Felix reads Raft certificates only at startup
([felix#2815](https://github.com/GetFelix/felix/issues/2815)), so until that
lands M1 restarts each control-plane member after its Raft renewal, every
12 hours. `nl-renew` asks the init for the restart; the init does nothing
else with it. Raft renewals are staggered by member an hour apart, so only
one member is down at a time. The cost: twice a day per member, the cluster
runs a few seconds with one member missing, which in a three-member cluster
leaves no spare vote, and a restart of the leader costs an election and a
few seconds without control-plane writes. Data-path traffic on the brokers
is not affected.

A machine that has been off for more than 24 hours has nothing valid to
renew with and rejoins with a new token. Node tokens and their refresh are
not certificates and stay with
[#73](https://github.com/GetFelix/ninelives/issues/73) and
[felixd#276](https://github.com/GetFelix/felixd/issues/276).

**Recovery** (S25):

- **A core, storage or worker machine.** The operator marks its node id
  revoked at the issuer. Its certificates are refused renewal and die within
  24 hours; its Felix principal is revoked in Felix, which ends its sessions
  sooner. It is wiped and rejoins with a new token and node id. No CA key
  was on it, so the CAs stay as they are and no other machine's certificates
  change.
- **A core machine, in addition.** It is removed from Raft membership first.
  Its Raft and broker certificates are not reissued under a new CA: they die
  within one lifetime, 24 hours at most, because renewal is refused. Until
  then a stolen Raft certificate can still speak for any member (T4), but
  it gets no Raft connection without the peer token, which is rotated
  ([felix#2813](https://github.com/GetFelix/felix/issues/2813)). Rotating
  the Raft CA would not end that window sooner: it needs a ceremony with an
  offline root, and new bundles reach a running member only by restart or
  rejoin, since bundles are read at startup. The tenant signing keys it held
  must still be rotated through Felix (T24, T25); that is Felix's gap, not
  the CA's.
- **An issuer machine.** Its intermediates must be treated as stolen. This
  is the only case in which a CA, intermediate or root, is rotated. Felix
  has no revocation, so a stolen intermediate stays trusted until it
  expires, up to a year, unless its root is replaced. Recovery is a new
  ceremony: new roots for every CA that issuer held and for the issuer CA,
  new bundles on every machine, and a restart of every Felix service, since
  bundles are read at startup. Until a trust update path exists (see
  Consequences), new bundles reach a machine only by wiping and rejoining
  it, one machine at a time.

**Later proofs of identity.** TPM attestation comes with measured boot
(M3, S13, S16) as a second factor: the issuer then also checks that the key
it signs lives in the machine's TPM and that the machine booted a NineLives
image. Cloud instance identity comes with cloud images (M5,
[#13](https://github.com/GetFelix/ninelives/issues/13)) as an alternative
to the token for machines an autoscaler starts, accepted only for an
account and image the operator listed at the issuer. Neither changes the
CAs or renewal.

## Options considered

| Option | Why not |
|---|---|
| **CA keys on the core machines** | No extra machines, but a core compromise then needs a new root and new trust everywhere, so S25 for a core machine becomes a whole-cluster re-key. It also makes the CA key a secret shared by 3 or 5 machines (S19), and adds a third way a core machine is the whole cluster on top of T24 and T25. Kept only for development clusters. |
| **An external KMS or HSM** | A key in a module cannot be copied out, but a compromised host signs whatever it likes while it holds access, so this shrinks recovery, not the attack. Cloud KMS ties the cluster to one provider and does not exist on bare metal or a Raspberry Pi. A PKCS#11 or TPM-held intermediate on the issuer is a later improvement to this option, not a replacement for it. |
| **step-ca (Apache-2.0) on the issuer machines** | The closest fit: one-time tokens, renewal over mTLS, refusing renewal as passive revocation, templates for the URI SAN. It loses on size and shape: one CA per instance, so three instances, their databases and configs, plus ACME, SSH and OIDC code the image does not need (S1), for a job that is "check the token or the current certificate against the registry, then sign." It is the fallback if `nl-issuer` grows. |
| **OpenBao PKI (MPL-2.0)** | A full secrets manager with its own storage, Raft and unsealing, so more machinery to secure than the problem it solves. |
| **No issuer: certificates made at the join and never renewed** | Breaks S19. Without revocation, only a short lifetime ends a stolen certificate. |

## Consequences

- A production cluster needs at least one issuer machine beyond its core
  machines. If every issuer is down, machines have 12 to 24 hours before
  certificates expire and the cluster stops; the issuer's health is
  alerted on. A second issuer, with its own intermediates, needs the
  registry shared between them, which this record does not design.
- Someone runs a ceremony every year and keeps four root keys offline.
  That is the cost of the roots never being online. The ceremony's tooling
  is designed with the issuer.
- `nl-issuer` and `nl-renew` are new components and get designs in
  `docs/components/` before code, including the registry format, the token
  format and the issuer's admin interface.
- Root rotation without a wipe needs a way to deliver new trust bundles that
  the join's "first boot only" rule (T71) and read-only trust directories do
  not allow today. Until one is designed, a root rotation is a rolling wipe
  and rejoin.
- The cluster CA's broker name is on every broker's certificate, so a
  client cannot tell one broker from another by certificate. Clients do not
  need to today, and each certificate still has its own key.

What Felix and felixd must add:

| Gap | Effect here | Issue |
|---|---|---|
| Raft (and bootstrap) TLS material is read once at startup | A 24-hour Raft certificate expires under a running control plane. M1 restarts each member after its Raft renewal (see Renewal). | [felix#2815](https://github.com/GetFelix/felix/issues/2815) |
| CA bundles are read once at startup, on every listener | A root rotation needs a restart of every Felix service | [felix#2816](https://github.com/GetFelix/felix/issues/2816) |
| The control-plane API takes no client CA | S10's mutual TLS cannot hold on that listener; callers are authenticated by tokens only | [felix#2811](https://github.com/GetFelix/felix/issues/2811) |
| The Raft peer token rotates only by restarting every member together | Rotating it after a core compromise is an outage | [felix#2813](https://github.com/GetFelix/felix/issues/2813) |
| Raft certificates are not bound to member ids | Any Raft certificate speaks for any member (T4). The issuer already puts the member id in the name, so the fix needs nothing from NineLives. | [felix#2802](https://github.com/GetFelix/felix/issues/2802) |
| felixd presents no client certificate | felixd's tokens are not bound to its machine (T30) | [felixd#275](https://github.com/GetFelix/felixd/issues/275) |

Felix does not need to become a CA, and needs no endpoint for certificates;
[#73](https://github.com/GetFelix/ninelives/issues/73) still needs one for
node tokens.

## Requires a change to S25

S25 in `docs/requirements.md`, as proposed on the threat model branch, has
a core-machine replacement rotate the Raft CA. With offline roots and
bundles read only at startup, that cannot be done in any useful time, and
under this record it is not needed. Once that branch merges, S25's core
machine clause changes to:

> For a core machine, in this order: the member is removed from Raft
> membership, so it receives nothing that follows; the Raft peer token is
> rotated, which in Felix today means restarting every remaining member
> together, an outage until
> [felix#2813](https://github.com/GetFelix/felix/issues/2813) lands; then
> every tenant signing key is staged, activated and the old one retired.
> The CAs are not rotated, since no CA key was on the machine: its Raft and
> broker certificates are refused renewal and expire within one certificate
> lifetime, 24 hours at most. A CA, intermediate or root, is rotated only
> when an issuer machine is compromised.

and its verification clause "for a core machine, so are the old Raft peer
token, certificates from the old Raft CA, and tokens signed by the old
keys" changes to "for a core machine, so are the old Raft peer token, its
old Raft certificate, and tokens signed by the old keys".

## Reopen when

- Felix adds certificate revocation or trust-bundle reload: either changes
  how long intermediates may be and how an issuer compromise is
  recovered.
- Felix re-reads Raft certificates
  ([felix#2815](https://github.com/GetFelix/felix/issues/2815)): the
  member restarts after renewal go away.
- `nl-issuer`'s design needs ACME, a database or more than a small policy,
  at which point step-ca is the better choice.
- The cost of extra issuer machines blocks the smallest production
  cluster.
- TPM attestation or cloud identity is designed, if either needs a change
  to the CAs or to renewal.
