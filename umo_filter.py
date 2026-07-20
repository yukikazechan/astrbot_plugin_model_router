"""Helpers for applying the router's UMO access-control configuration."""

from collections.abc import Mapping
from typing import Any


BLACKLIST_MODE = "blacklist"
WHITELIST_MODE = "whitelist"
FILTER_MODES = (BLACKLIST_MODE, WHITELIST_MODE)


def get_filter_mode(session_config: Mapping[str, Any] | None) -> str:
    """Return a supported filter mode, defaulting safely to blacklist mode."""
    if not isinstance(session_config, Mapping):
        return BLACKLIST_MODE

    mode = str(session_config.get("filter_type", BLACKLIST_MODE)).strip().lower()
    return mode if mode in FILTER_MODES else BLACKLIST_MODE


def normalize_umo_list(value: Any) -> list[str]:
    """Normalize user-provided UMO entries while preserving their order."""
    if not isinstance(value, (list, tuple, set)):
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for entry in value:
        if not isinstance(entry, str):
            continue
        umo = entry.strip()
        if umo and umo not in seen:
            normalized.append(umo)
            seen.add(umo)
    return normalized


def get_active_umo_list(session_config: Mapping[str, Any] | None) -> list[str]:
    """Return the normalized list used by the configured filter mode."""
    if not isinstance(session_config, Mapping):
        return []

    return normalize_umo_list(session_config.get(get_filter_mode(session_config), []))


def is_umo_allowed(umo: str, session_config: Mapping[str, Any] | None) -> bool:
    """Return whether model routing should run for an exact UMO value."""
    normalized_umo = str(umo).strip()
    mode = get_filter_mode(session_config)
    matched = normalized_umo in get_active_umo_list(session_config)
    return matched if mode == WHITELIST_MODE else not matched
