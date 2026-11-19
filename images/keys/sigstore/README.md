# Sigstore trusted root

`trusted_root.json` holds the public keys and certificates the Sigstore public
good instance signs with: Fulcio's certificate chain, Rekor's and the
certificate transparency log's keys, and the timestamp authority's chain. The
fetch phase verifies a component's Sigstore bundle against this file and
nothing else, so it never refreshes anything over the network.

Its SHA-256 is in `images/tools.toml` under `[sigstore]`, and the fetch refuses
a file that does not match.

## Where it came from

`targets/trusted_root.json` in https://github.com/sigstore/root-signing, at
commit `c9bda74ad2221f938f7d2e0295ca3aad2da710a8` (2025-09-22), the latest
commit that touched it. That repository holds the targets of Sigstore's TUF
root.

## Updating it

Sigstore rotates these keys now and then. Copy the new file from the same
path, update the hash and the commit above, and check that the bundles of
every pinned component still verify (`python3 scripts/fetch_image.py` does).
Change it only in a pull request that touches nothing else.
