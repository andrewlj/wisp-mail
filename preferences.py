"""
wisp-mail preferences — classification cache (Phase 1) + rule table (Phase 2).

Phase 1 only needs the cache half: a classification result is permanent (an
email's content doesn't change), so once classified it's never re-sent to the
LLM. The rule table / candidate-review queue — which decides what ACTION a
category maps to, re-evaluated fresh against current rules every run — lands
in Phase 2. See the design doc's "核心原则 04": understanding and deciding
are deliberately decoupled, understand once, decide every time.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

_CONFIG_PATH = Path(__file__).parent / "config.yaml"
with open(_CONFIG_PATH) as f:
    _cfg = yaml.safe_load(f)

WORKSPACE  = Path(_cfg.get("workspace", "~/wisp-mail")).expanduser()
_CACHE_DIR = WORKSPACE / "cache"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cache_path(message_id: str) -> Path:
    # message ids can contain characters that aren't safe as filenames
    # (slashes in particular show up in real Message-IDs) — hash instead of
    # sanitizing, so this can never collide with path traversal or a
    # filesystem-reserved name.
    digest = hashlib.sha256(message_id.encode("utf-8")).hexdigest()[:24]
    return _CACHE_DIR / f"{digest}.json"


def get_cached(message_id: str) -> dict | None:
    """Return the cached classification for message_id, or None if never
    classified before."""
    p = _cache_path(message_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def store_classification(message_id: str, sender: str, subject: str,
                         category: str, confidence: float, reasoning: str,
                         unread: bool | None = None,
                         status: str = "classified") -> None:
    """Persist a classification result. Permanent — content doesn't change,
    so this is never invalidated by time, only overwritten if re-stored
    with a new status (e.g. once an action is actually taken).

    `unread` is recorded as a plain attribute of the message at classification
    time (like sender/subject) — it must never gate WHETHER a message gets
    classified in the first place. None if the caller didn't have it."""
    from datetime import datetime
    data = {
        "message_id": message_id,
        "sender": sender,
        "subject": subject,
        "unread": unread,
        "category": category,
        "confidence": confidence,
        "reasoning": reasoning,
        "status": status,   # classified | executed | kept
        "classified_at": datetime.now().isoformat(timespec="seconds"),
    }
    _cache_path(message_id).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def list_all() -> list[dict]:
    """Return every cached classification, newest first. Used by status.py
    to browse results — the cache files themselves are hash-named and not
    meant to be read directly."""
    records = []
    for p in _CACHE_DIR.glob("*.json"):
        try:
            records.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    records.sort(key=lambda r: r.get("classified_at", ""), reverse=True)
    return records


def update_status(message_id: str, status: str) -> None:
    """Update just the status field of an already-cached classification
    (e.g. 'classified' -> 'executed' once Phase 2 acts on it)."""
    data = get_cached(message_id)
    if data is None:
        return
    data["status"] = status
    _cache_path(message_id).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
