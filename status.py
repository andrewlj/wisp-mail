#!/usr/bin/env python3
"""
wisp-mail status — browse what run.py has classified so far.

The cache itself is hash-named JSON files (see preferences._cache_path) —
not meant to be read directly. This is the human-facing view of it.

Usage:
    python status.py                  # everything, grouped by category
    python status.py --category 广告推广
    python status.py --unread-only    # only messages that were unread when classified
    python status.py --limit 20       # cap how many rows print per category
"""

from __future__ import annotations

import argparse
import sys

import preferences


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--category", default="", help="only show one category")
    ap.add_argument("--unread-only", action="store_true",
                    help="only show messages that were unread when classified")
    ap.add_argument("--limit", type=int, default=10,
                    help="max rows to print per category (default 10)")
    args = ap.parse_args()

    records = preferences.list_all()
    if args.category:
        records = [r for r in records if r.get("category") == args.category]
    if args.unread_only:
        records = [r for r in records if r.get("unread")]

    if not records:
        print("no classified messages yet — run.py hasn't processed anything "
              "matching this filter")
        return 0

    by_category: dict[str, list[dict]] = {}
    for r in records:
        by_category.setdefault(r.get("category", "其他"), []).append(r)

    print(f"{len(records)} classified message(s) total\n")
    for cat, rows in sorted(by_category.items(), key=lambda kv: -len(kv[1])):
        print(f"{cat}  ({len(rows)})")
        for r in rows[:args.limit]:
            tag = "unread" if r.get("unread") else "read  "
            status = r.get("status", "classified")
            print(f"  [{tag} {r.get('confidence', 0):.2f} {status:10s}] "
                  f"{r.get('sender', '')[:40]} | {r.get('subject', '')[:50]}")
        if len(rows) > args.limit:
            print(f"  … and {len(rows) - args.limit} more (raise --limit to see them)")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
