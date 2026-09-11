from __future__ import annotations

import hashlib


TARGET_KEY_LENGTH = 272
TARGET_NAME_LENGTH = 256
TARGET_IDENTITY_LENGTH = 256


def normalize_target_name(value: str | None) -> str:
    return str(value or "").strip().casefold()


def normalize_target_identity(value: str | None) -> str:
    return str(value or "").strip()


def task_target_key(target_name: str | None, sec_uid: str | None = None) -> str:
    """Return the schedule identity used by the database uniqueness constraint."""
    stable_target = normalize_target_identity(sec_uid)
    if stable_target:
        digest = hashlib.sha256(stable_target.encode("utf-8")).hexdigest()
        return f"uid:{digest}"
    normalized_name = normalize_target_name(target_name)
    digest = hashlib.sha256(normalized_name.encode("utf-8")).hexdigest()
    return f"name:{digest}"


def legacy_task_target_key(task_id: str) -> str:
    """Give a pre-existing duplicate an immutable key without removing user data."""
    return f"legacy:{hashlib.sha256(str(task_id).encode('utf-8')).hexdigest()}"


def targets_conflict(
    left_name: str | None,
    left_sec_uid: str | None,
    right_name: str | None,
    right_sec_uid: str | None,
) -> bool:
    """Compare schedule targets while preserving compatibility with manual names."""
    left_identity = normalize_target_identity(left_sec_uid)
    right_identity = normalize_target_identity(right_sec_uid)
    if left_identity and right_identity:
        return left_identity == right_identity
    return normalize_target_name(left_name) == normalize_target_name(right_name)


def task_target_key_default(context) -> str:
    parameters = context.get_current_parameters()
    return task_target_key(parameters.get("target_name"))
