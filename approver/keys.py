"""Approver key files (`make keys`). The private key never leaves approver/ (invariant 7)."""

import os
import sys
from pathlib import Path

from nacl.signing import SigningKey

PRIVATE_NAME = "approver.key"
PUBLIC_NAME = "approver.pub"


def generate_keys(directory: str | Path = "keys") -> tuple[Path, Path]:
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    priv, pub = d / PRIVATE_NAME, d / PUBLIC_NAME
    if priv.exists() or pub.exists():
        raise FileExistsError(f"refusing to overwrite keys in {d}")
    key = SigningKey.generate()
    fd = os.open(priv, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(bytes(key).hex())
    pub.write_text(key.verify_key.encode().hex())
    return priv, pub


def load_signing_key(path: str | Path) -> SigningKey:
    return SigningKey(bytes.fromhex(Path(path).read_text().strip()))


if __name__ == "__main__":
    try:
        p, q = generate_keys(sys.argv[1] if len(sys.argv) > 1 else "keys")
    except FileExistsError as exc:
        print(exc)
        sys.exit(0)
    print(f"wrote {p} (private, 0600) and {q} (public)")
