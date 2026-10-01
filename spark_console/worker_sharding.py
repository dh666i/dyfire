"""Stable account-to-worker shard assignment.

The built-in ``hash()`` function is deliberately unsuitable here because its
seed changes between Python processes.  A persisted key derived from the
account id keeps every worker and database dialect on the same assignment.
"""

from __future__ import annotations

import hashlib


MAX_SHARDS = 1024


def validate_shard(shard: int, shards: int) -> tuple[int, int]:
    """Validate and normalize a worker's zero-based shard configuration."""
    try:
        shard = int(shard)
        shards = int(shards)
    except (TypeError, ValueError) as error:
        raise ValueError("worker shard values must be integers") from error
    if shards < 1 or shards > MAX_SHARDS:
        raise ValueError(f"worker shards must be between 1 and {MAX_SHARDS}")
    if shard < 0 or shard >= shards:
        raise ValueError("worker shard must be between 0 and worker shards - 1")
    return shard, shards


def account_dispatch_key(account_id: str) -> int:
    """Return a stable non-negative 63-bit key for an account identifier."""
    digest = hashlib.sha256(str(account_id).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") & ((1 << 63) - 1)


def account_shard(account_id: str, shards: int) -> int:
    """Return the shard that owns an account for a given shard count."""
    _, shards = validate_shard(0, shards)
    return account_dispatch_key(account_id) % shards


def owns_account(account_id: str, shard: int, shards: int) -> bool:
    """Return whether ``shard`` is responsible for ``account_id``."""
    shard, shards = validate_shard(shard, shards)
    return account_shard(account_id, shards) == shard
