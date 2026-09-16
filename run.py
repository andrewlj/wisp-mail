#!/usr/bin/env python3
"""
wisp-mail — Phase 1: understand-only dry run.

Fetches ALL mail (read and unread alike — always) and classifies every
message that hasn't been classified before (cache hit -> skip the LLM call
entirely). Does NOT delete, move, or otherwise act on anything — Phase 1's
whole job is validating that classification itself holds up and that
per-email isolation genuinely works, with zero side effects while that's
being checked.

Read/unread is recorded as a plain attribute of each classified message (see
preferences.store_classification's `unread` field) — like sender or subject,
never a gate on whether the message gets fetched or classified in the first
place. There is deliberately no "--unread-only" flag here: that would put
read status back in the role of deciding what's worth understanding, which is
exactly the wrong model (a message's read state on your phone has nothing to
do with whether wisp-mail should have an opinion about it).

Usage:
    python run.py                  # classify up to `per_run_limit` new messages
    python run.py --limit 10       # override the per-run cap for this run
    python run.py --account you@example.com

Known limitation — not yet fixed: `list_mail`'s underlying AppleScript call
gets slower with how much it lists (~13s for 300 messages, read+unread; a
full ~2000+ message backlog would exceed its own 60s timeout and fail
outright). `--list-limit` bounds how many messages are even CONSIDERED per
run, separately from `--limit`/per_run_limit which bounds how many get
CLASSIFIED. There's no pagination yet, so anything beyond `--list-limit` in
Mail's internal ordering is never seen at all, no matter how many times this
runs — only the classification pace within that window is spread across
runs. Fine for staying current with new mail; not yet a real answer for
"classify my whole multi-thousand-message backlog."
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

# One line of list_mail() output looks like (mark is "● " for unread, "  " —
# two spaces — for already-read, stripped off before this pattern is tried):
#   sender@example.com | Subject text here | 2026-09-16 08:31 | id:abc123@mail.example.com
_LINE_RE = re.compile(
    r"^(?P<sender>.+?)\s*\|\s*(?P<subject>.+?)\s*\|\s*(?P<date>\S+ \S+)\s*\|\s*id:(?P<id>\S+)\s*$"
)
_HEADER_RE = re.compile(r"^\[(?P<account>[^\]]+)\]\s+(?P<count>\d+)\s+(?:unread\s+)?message")


def _parse_messages(raw: str) -> tuple[list[dict], int | None]:
    """Parse list_mail() output into structured rows (read and unread alike)
    plus, if present, the account header's total count — used only to report
    an honest "N more exist beyond what --list-limit fetched" caveat, not to
    fetch them (see the module docstring's known limitation).

    Lines that don't match the expected shape (account headers, error lines,
    a mailbox-not-found message) are silently skipped, not treated as
    messages — better to under-process than to crash on a format surprise.
    """
    rows: list[dict] = []
    header_total: int | None = None
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        h = _HEADER_RE.match(stripped)
        if h:
            header_total = (header_total or 0) + int(h.group("count"))
            continue
        is_unread = stripped.startswith("●")
        content = stripped[1:].strip() if is_unread else stripped
        m = _LINE_RE.match(content)
        if m:
            row = m.groupdict()
            row["unread"] = is_unread
            rows.append(row)
    return rows, header_total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="", help="limit to one account email")
    ap.add_argument("--limit", type=int, default=None,
                    help="override config's mail.per_run_limit for this run "
                         "(how many NEW messages get classified)")
    ap.add_argument("--list-limit", type=int, default=None,
                    help="override config's mail.list_limit for this run "
                         "(how many messages are even considered — see the "
                         "module docstring's known pagination limitation)")
    args = ap.parse_args()

    with open(_CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)
    mail_cfg = cfg.get("mail", {})
    per_run_limit = args.limit if args.limit is not None else \
        int(mail_cfg.get("per_run_limit", 50))
    list_limit = args.list_limit if args.list_limit is not None else \
        int(mail_cfg.get("list_limit", 300))

    print(f"fetching mail (account={args.account or 'all'}, "
          f"list_limit={list_limit})…")
    raw = mail_tools.list_mail(account=args.account, mailbox="INBOX",
                               limit=list_limit, unread_only=False)
    rows, header_total = _parse_messages(raw)
    print(f"found {len(rows)} message(s) in the listing")
    if header_total is not None and header_total > len(rows):
        print(f"  note: the mailbox has {header_total} total — only the "
              f"first {len(rows)} (list_limit) were considered this run; "
              f"the rest aren't reachable yet (see known limitation)")
    print()

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
            reasoning=result["reasoning"], unread=row["unread"])

        processed += 1
        tag = "unread" if row["unread"] else "read  "
        print(f"[{tag} {result['category']:6s} {result['confidence']:.2f}] "
              f"{row['sender']} | {row['subject']}")
        print(f"                  {result['reasoning']}")

    remaining = max(0, len(rows) - skipped_cached - processed)
    print(f"\n{'─' * 50}")
    print(f"classified {processed} new message(s), {skipped_cached} already "
          f"cached from a prior run"
          + (f", {remaining} left in this listing for the next run "
             f"(per-run cap {per_run_limit})" if remaining else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
