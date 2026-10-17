"""File fingerprints (fast, at scan time) and full SHA-256 (lazy, at processing time)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import xxhash

CHUNK = 4 * 1024 * 1024


def fingerprint(path: Path) -> str:
    """xxhash of the first, middle and last 4 MB plus the size.

    Reads at most 12 MB, so scanning a 15 TB archive over SMB stays practical.
    Collisions are resolved with a full SHA-256.
    """
    size = path.stat().st_size
    h = xxhash.xxh3_128()
    h.update(size.to_bytes(8, "little"))
    with path.open("rb") as f:
        if size <= 3 * CHUNK:
            h.update(f.read())
        else:
            for offset in (0, size // 2 - CHUNK // 2, size - CHUNK):
                f.seek(offset)
                h.update(f.read(CHUNK))
    return h.hexdigest()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()
