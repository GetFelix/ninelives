"""Verify a component's signed SHA256SUMS (docs/decisions/0004).

The Sigstore bundle on SHA256SUMS is checked by cosign, a pinned binary
(images/tools.toml), against the trusted root pinned in images/keys/sigstore/.
With both given on the command line cosign reads nothing from the network; the
environment below also points its proxies at a closed port, so a change that
made it try would fail instead of quietly reaching out.

The signature binds the certificate's identity and issuer to the SHA256SUMS
bytes. The artifact is then looked up in those bytes by file name, and its hash
has to equal the line and the pin in components.toml.
"""
import os
import pathlib
import re
import subprocess

LINE = re.compile(r"([0-9a-f]{64}) [ *](\S+)")


class VerifyError(Exception):
    pass


def parse_sums(text: str) -> dict[str, str]:
    """SHA256SUMS lines as {file name: sha256}."""
    sums: dict[str, str] = {}
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        m = LINE.fullmatch(line)
        if not m:
            raise VerifyError(f"SHA256SUMS line {n} is not '<sha256>  <file>'")
        if m.group(2) in sums:
            raise VerifyError(f"SHA256SUMS lists {m.group(2)} twice")
        sums[m.group(2)] = m.group(1)
    return sums


def verify_bundle(cosign: pathlib.Path, root: pathlib.Path, bundle: pathlib.Path,
                  checksums: pathlib.Path, identity: str, issuer: str) -> None:
    """Raise VerifyError unless bundle signs these exact bytes as identity from issuer."""
    # Exact match: cosign's *-regexp flags are the only way to match loosely.
    cmd = [str(cosign), "verify-blob", "--bundle", str(bundle), "--trusted-root", str(root),
           "--certificate-identity", identity, "--certificate-oidc-issuer", issuer, str(checksums)]
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "TMPDIR", "SSL_CERT_FILE")}
    env.update(HTTPS_PROXY="http://127.0.0.1:9", HTTP_PROXY="http://127.0.0.1:9", NO_PROXY="")
    try:
        r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise VerifyError(f"could not run cosign: {e}") from e
    if r.returncode != 0:
        detail = (r.stderr.strip().splitlines() or ["no output"])[0]
        raise VerifyError(f"the Sigstore bundle does not verify for {identity} from {issuer}: {detail}")


def check_artifact(sums: dict[str, str], filename: str, pinned: str, actual: str) -> None:
    """The downloaded file, the pin and the signed list must all name one hash."""
    signed = sums.get(filename)
    if signed is None:
        raise VerifyError(f"the signed SHA256SUMS does not list {filename}")
    if signed != pinned:
        raise VerifyError(f"{filename}: components.toml pins {pinned}, the signed SHA256SUMS says {signed}")
    if actual != signed:
        raise VerifyError(f"{filename}: downloaded {actual}, the signed SHA256SUMS says {signed}")
