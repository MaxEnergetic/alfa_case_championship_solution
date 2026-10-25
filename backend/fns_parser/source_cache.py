"""Reuse recent complete source snapshots during a retry, retaining their dates."""
from copy import deepcopy
from datetime import datetime, timezone


def recent_entry(entry, *, timestamp, max_age_seconds=21600):
    if not isinstance(entry, dict) or entry.get("status") not in {"ok", "not_found", "not_applicable"}:
        return None
    if entry.get("error"):
        return None
    try:
        fetched = datetime.fromisoformat(entry[timestamp])
        age = (datetime.now(timezone.utc) - fetched).total_seconds()
    except (KeyError, TypeError, ValueError):
        return None
    if not 0 <= age <= max_age_seconds:
        return None
    cached = deepcopy(entry)
    cached["cache_reused"] = True
    return cached


def previous_entity(previous, query):
    # Batch requests use an exact OGRN. Never reuse an incomplete INN search
    # that may have omitted another historical individual registration.
    if not isinstance(previous, dict) or previous.get("schema_version") != "1.0":
        return None
    if previous.get("query") != query or len(query) not in (13, 15):
        return None
    entities = previous.get("entities", [])
    if len(entities) != 1 or entities[0].get("ogrn") != query:
        return None
    return entities[0]
