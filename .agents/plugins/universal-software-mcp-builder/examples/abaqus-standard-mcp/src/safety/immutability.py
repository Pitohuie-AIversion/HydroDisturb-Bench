"""
Baseline Immutability & Copy-on-Write (CoW) Safety Protocol.
Protects golden source models from destructive overwrites.
"""
import hashlib
from pathlib import Path

def compute_sha256(path: Path) -> str:
    """Computes SHA-256 hash of a file for immutability verification."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()

def assert_cow(source: Path, destination: Path) -> None:
    """Enforces that destination is strictly distinct from source (Copy-on-Write)."""
    if source.resolve() == destination.resolve():
        raise ValueError(
            f"Copy-on-Write violation: Target path {destination} cannot equal source path {source}!"
        )
