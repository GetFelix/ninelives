# 0009: Node tokens come from Felix's token exchange, with the issuer as identity provider

**Status:** Accepted, 2026-11-19. Answers the minting half of
[#73](https://github.com/GetFelix/ninelives/issues/73) for M1.

## Context

[0008](0008-ca-keys-and-machine-certificates.md) decides how a machine gets
and renews its certificates. It leaves node tokens open: "Felix does not need
to become a CA ... [#73](https://github.com/GetFelix/ninelives/issues/73)
still needs [an endpoint] for node tokens."

Every machine needs Felix tokens: felixd a broker-audience token
(`FELIX_AUTH_TOKEN_FILE`), a broker its node credential with audience
`felix-controlplane` (`FELIX_NODE_TOKEN_FILE`, or a refresh token in
`FELIX_NODE_REFRESH_TOKEN_FILE`, which the broker refreshes itself). Felix
0.7.2 mints tokens only through its control plane: exchange of a token from a
tenant-trusted OIDC identity provider, refresh, delegation, and bootstrap
([Felix auth](https://github.com/GetFelix/felix/blob/v0.7.2/docs/auth.md)).
Nothing takes a join token.

S19 wants one principal per machine, revocable alone. S10 binds a token to
the client certificate issued to its subject (`felix:principal:<sub>`).

## Decision

**Felix tokens are derived from the machine's certificates, through Felix's
existing token exchange, with `nl-issuer` as the cluster tenant's identity
provider for machine principals.** The join never handles a Felix token.

- Each Felix-facing service on a machine is its own principal, with subject
  `nl/<node id>/<incarnation>/<service>`. The issuer computes Felix's
  principal id, `sha256(iss + "|" + sub)`, and writes it into that service's
  client certificate as the `felix:principal:` URI.
- A service's renewal helper asks the issuer, over mutual TLS with the
  service's current certificate, for a 60-second ES256 assertion (usable for 120 seconds, given Felix's 60-second leeway) naming that
  subject, with groups from the issuer's registry. The issuer answers only
  for an active node's current incarnation and current serial, the same
  gate as renewal.
- The helper exchanges the assertion at the control plane and writes the
  access and refresh tokens into the service's directory. Refresh then runs
  as Felix already does it.
- The cluster tenant trusts the issuer with its public keys configured
  inline, so the control plane fetches nothing
  ([felix#2836](https://github.com/GetFelix/felix/issues/2836)). Grants are
  made to the issuer's groups (`group:<iss>#felixd`,
  `group:<iss>#node-<id>`), never to a principal by name. The issuer holds
  no Felix token or admin credential, but its assertion key works as a
  Felix credential for exactly the grants made to its groups: it can sign
  any subject with any of them. An issuer compromise therefore yields every
  grant made to `group:<iss>#*`, which is felixd's node role and
  `node.manage` over every node, and nothing else in Felix.
- The image never sets `FELIX_CONTROLPLANE_LEGACY_UNSCOPED_GROUPS`, which
  would also link the issuer's groups under bare names, and its config check
  fails if it is set.

The full flow is in the [join design](../components/join.md#felix-tokens-after-the-join).

## Options considered

| Option | Why not |
|---|---|
| **A Felix endpoint that accepts join tokens** | Puts join-token checking on core machines, which already hold every tenant's signing keys (T24). Felix would need a token store and the issuer's registry, or a second copy of both. 0008 kept issuance off core machines for exactly this reason. |
| **The issuer mints tokens with a Felix admin credential** | Minting arbitrary tokens takes cluster-admin rights in Felix: tenant administration, RBAC changes, every tenant. An issuer compromise would then be a Felix admin compromise. With this decision it yields only the grants made to the issuer's groups, which are the machine identities the issuer can already create through certificates (0008). |
| **The join receives a refresh token from the issuer** | The issuer would have to obtain it from Felix somehow (one of the two rows above), and the join, which runs before the first core machine's control plane exists, could not get one at formation anyway. |
| **Refresh tokens provisioned on the config partition** | A 30-day secret on a partition anyone with the disk reads before first boot (X7), for every machine. |
| **Felix exchanges a client certificate directly for a token** | The cleanest end state: no assertion key at all. It needs a larger Felix feature (certificate-to-principal mapping and grants per certificate kind) than inline keys do, and the issuer already gates every certificate. Revisit if Felix builds it. |

## Consequences

- No new Felix endpoint. Felix needs inline identity-provider keys
  ([felix#2836](https://github.com/GetFelix/felix/issues/2836)), and its
  exchange should refuse a reused assertion (`jti`, same issue).
- The issuer holds one more key, the assertion-signing key, with the same
  custody as its intermediates: online on the issuer, replaced at the yearly
  ceremony, and recovered the same way after an issuer compromise. Rotation
  overlaps by configuring two keys at the control plane.
- Whoever holds `tenant.manage` on the cluster tenant can replace the
  issuer's keys and so mint machine principals. The cluster tenant has no
  tenant admins except operators.
- A machine has one principal (felixd) or two (felixd and the broker), so
  revoking a machine is one issuer call and one Felix revocation per
  principal. The operator tool does both, and waits 150 seconds between
  them, because Felix's revocation cutoff does not refuse an assertion
  issued before it ([felix#2839](https://github.com/GetFelix/felix/issues/2839)).
- M1 is blocked on [felix#2836](https://github.com/GetFelix/felix/issues/2836),
  with no fallback.
- A storage or core token's creation also writes one Felix grant,
  `node.manage` over `node:<id>` for `group:<iss>#node-<id>`, with the
  operator's own Felix credential.
- felixd's token is refreshed by its renewal helper until felixd refreshes
  it itself ([felixd#276](https://github.com/GetFelix/felixd/issues/276)).

## Amended 2026-11-19

Felix changes on main after v0.7.2 resolve the two M1 blockers.

- **Inline keys and single-use assertions**
  ([felix#2836](https://github.com/GetFelix/felix/issues/2836), merged as
  [felix#2843](https://github.com/GetFelix/felix/pull/2843)). The cluster
  tenant's issuer config sets `jwks`, `single_use_jti` and
  `max_token_lifetime_seconds: 60`. The `jti` record is in memory per
  control-plane replica, so a replayed assertion succeeds once on each
  replica that has not seen it: 2 extra on the 3-core cluster, within 120
  seconds ([felix#2844](https://github.com/GetFelix/felix/issues/2844)).
  Changing the keys needs `tenant.manage:cluster:*`, not `tenant.manage`.
  Taking an inline key out revokes every principal in the tenant, operators
  included ([felix#2850](https://github.com/GetFelix/felix/issues/2850)), so
  retiring the old assertion key is a planned cluster-wide re-exchange.
- **The internal role** ([felix#2840](https://github.com/GetFelix/felix/issues/2840),
  merged as [felix#2841](https://github.com/GetFelix/felix/pull/2841)).
  Core brokers run with `FELIX_NODE_ROLE=internal` and hold no shard.
  Clearing the role needs `node.manage` on `cluster:*`; the issuer's groups
  hold it only over `node:<id>`, so an issuer compromise cannot lift it.
- **Releases.** M1 needs a Felix release that contains both, and pins the
  first one, v0.8.0-preview.1, in `components.toml` under
  [0004](0004-components-from-pinned-releases.md). A nightly cannot be pinned.
  The sentence "M1 is blocked on felix#2836, with no fallback" now reads:
  M1 needs that release, with no fallback.
- **Operator certificates.** With the control-plane API's client CA
  ([felix#2821](https://github.com/GetFelix/felix/pull/2821)), the operator's
  grant writes and Felix revocation need a client certificate issued to
  `felix:principal:<operator>`; the operator tooling design owns issuing it.

## Reopen when

- Felix offers a certificate-to-token exchange, or certificate-bound
  principals without tokens.
- Felix moves signing keys off core machines
  ([felix#2801](https://github.com/GetFelix/felix/issues/2801)) in a way that
  changes where minting should live.
- Cloud instance identity (M5) or TPM attestation (M3) needs tokens before a
  certificate exists.
