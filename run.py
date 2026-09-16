#!/usr/bin/env python3
"""
wisp-mail — Phase 1: understand-only dry run.

Fetches unread mail, classifies every message that hasn't been classified
before (cache hit -> skip the LLM call entirely), and prints what it found.
Does NOT delete, move, or otherwise act on anything — Phase 1's whole job is
validating that classification itself holds up and that per-email isolation
genuinely works, with zero side effects while that's being checked.

Usage:
    python run.py                  # classify up to `per_run_limit` new unread
    python run.py --limit 10       # override the per-run cap for this run
    python run.py --account you@example.com
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

import mail_tools
import preferences
from classify import classify_email

_CONFIG_PATH = Path(__file__).parent / "config.yaml"

# One line of list_mail() output for an unread message looks like:
#   ● sender@example.com | Subject text here | 2026-09-16 08:31 | id:abc123@mail.example.com
_LINE_RE = re.compile(
    r"^●\s*(?P<sender>.+?)\s*\|\s*(?P<subject>.+?)\s*\|\s*(?P<date>\S+ \S+)\s*\|\s*id:(?P<id>\S+)\s*$"
)


def _parse_unread(raw: str) -> list[dict]:
    """Parse mail_tools.list_mail(unread_only=True) output into structured
    rows. Lines that don't match the expected shape (account headers, error
    lines, a mailbox-not-found message) are silently skipped, not treated as
    messages — better to under-process than to crash on a format surprise."""
    rows = []
    for line in raw.splitlines():
        m = _LINE_RE.match(line.strip())
        if m:
            rows.append(m.groupdict())
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="", help="limit to one account email")
    ap.add_argument("--limit", type=int, default=None,
                    help="override config's mail.per_run_limit for this run")
    args = ap.parse_args()

    with open(_CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)
    per_run_limit = args.limit if args.limit is not None else \
        int(cfg.get("mail", {}).get("per_run_limit", 50))

    print(f"fetching unread mail (account={args.account or 'all'})…")
    raw = mail_tools.list_mail(account=args.account, mailbox="INBOX",
                               limit=500, unread_only=True)
    rows = _parse_unread(raw)
    print(f"found {len(rows)} unread message(s) in the listing\n")

    processed = 0
    skipped_cached = 0
    for row in rows:
        mid = row["id"]
        if preferences.get_cached(mid) is not None:
            skipped_cached += 1
            continue
        if processed >= per_run_limit:
            break

        body_full = mail_tools.read_mail(message_id=mid, account=args.account)
        result = classify_email(sender=row["sender"], subject=row["subject"],
                                body=body_full, date=row["date"])
        preferences.store_classification(
            message_id=mid, sender=row["sender"], subject=row["subject"],
            category=result["category"], confidence=result["confidence"],
            reasoning=result["reasoning"])

        processed += 1
        print(f"[{result['category']:6s} {result['confidence']:.2f}] "
              f"{row['sender']} | {row['subject']}")
        print(f"         {result['reasoning']}")

    remaining = max(0, len(rows) - skipped_cached - processed)
    print(f"\n{'─' * 50}")
    print(f"classified {processed} new message(s), {skipped_cached} already "
          f"cached from a prior run"
          + (f", {remaining} left for the next run (per-run cap {per_run_limit})"
             if remaining else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
