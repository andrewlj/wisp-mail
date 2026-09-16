"""
wisp-mail mail I/O — macOS Mail.app access over AppleScript.

Extracted from wisp-agent's tools.py: the locale-aware mailbox alias
resolution (works across accounts/languages — 'INBOX'/'Junk'/'收件箱'/
'垃圾邮件' all resolve regardless of the account's display language) and the
read-only list/read operations. This is the module boundary the design calls
out for eventual reuse — wisp-agent's own mail tools are meant to eventually
call into wisp-mail instead of keeping a second copy of this logic (see the
design doc's "wisp 集成策略"), so this file is deliberately a faithful,
unmodified port of the AppleScript itself — only the wrapping (dropped the
`tool_` naming/schema convention, since wisp-mail has no agentic tool-calling
loop to register these with) changed.

Phase 1 scope: list + read only. Move/delete land in Phase 2 once there's a
real action layer (rules/execution) to wire them to.
"""

from __future__ import annotations

import subprocess


def _run(cmd: list[str], timeout: int = 30) -> str:
    """Run a subprocess, return combined stdout/stderr + exit code string."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        parts = []
        if p.stdout: parts.append(p.stdout.rstrip())
        if p.stderr: parts.append(f"[stderr]\n{p.stderr.rstrip()}")
        parts.append(f"[exit {p.returncode}]")
        return "\n".join(parts)
    except subprocess.TimeoutExpired:
        return f"error: timed out after {timeout}s"
    except Exception as e:
        return f"error: {e}"


def _osascript_clean(script: str, timeout: int = 15) -> str:
    """Run AppleScript; return stdout only (strips the [exit N] suffix)."""
    result = _run(["osascript", "-e", script], timeout=timeout)
    if "[exit 0]" in result:
        return result.split("[exit 0]")[0].strip()
    return result  # error case: caller sees the raw error


def _mail_where(subject: str = "", sender: str = "", message_id: str = "") -> str:
    """AppleScript whose-clause for locating a message.

    Prefers `message id` (exact, stable RFC Message-ID) when provided; this is
    the reliable primary key. Falls back to subject/sender substring matching,
    which is brittle (whitespace/emoji differences break it) but kept as a
    convenience when no id is available.
    """
    if message_id:
        mid = message_id.replace('"', '\\"')
        return f'whose message id is "{mid}"'
    s = subject.replace('"', '\\"')
    if sender:
        snd = sender.replace('"', '\\"')
        return f'whose subject contains "{s}" and sender contains "{snd}"'
    return f'whose subject contains "{s}"'


# Semantic mailbox resolution — provider folder names differ per account and
# locale. Tools resolve these aliases to the right folder within the
# message's own account, rather than requiring an exact name match.
_MBOX_JUNK_NAMES = [
    "Junk", "Spam", "Junk E-mail", "Junk Email", "Bulk Mail",
    "垃圾邮件", "垃圾箱", "垃圾郵件",
]
_MBOX_TRASH_NAMES = [
    "Trash", "Deleted Messages", "Deleted Items", "Bin",
    "已删除邮件", "已删除", "废纸篓", "已刪除郵件",
]
# Gmail has no real Archive folder — archiving = removing the INBOX label, which
# leaves the message in "All Mail" / "所有邮件". Map archive there as the closest
# equivalent; Hotmail/Exchange use a literal Archive ("存档") folder.
_MBOX_ARCHIVE_NAMES = [
    "Archive", "Archived", "存档", "归档", "封存",
    "All Mail", "所有邮件", "所有郵件",
]
_MBOX_INBOX_NAMES = [
    "INBOX", "Inbox", "inbox", "收件箱", "收件匣", "受信トレイ", "Posteingang",
]
_MBOX_SENT_NAMES = [
    "Sent", "Sent Mail", "Sent Messages", "Sent Items",
    "已发送邮件", "已发邮件", "已发送", "已寄郵件", "寄件備份", "寄件備份匣",
]
_MBOX_DRAFTS_NAMES = [
    "Drafts", "Draft", "草稿", "草稿箱", "草稿邮件", "草稿匣",
]
# Aliases the user/agent may type → canonical category
_MBOX_ALIAS = {
    "junk": "junk", "spam": "junk", "垃圾": "junk", "垃圾箱": "junk",
    "垃圾邮件": "junk", "广告": "junk",
    "trash": "trash", "bin": "trash", "废纸篓": "trash", "删除": "trash",
    "已删除": "trash", "已删除邮件": "trash", "回收站": "trash",
    "archive": "archive", "archived": "archive", "存档": "archive",
    "归档": "archive", "封存": "archive", "all mail": "archive",
    "所有邮件": "archive",
}
# Source-side aliases (for reading FROM a folder). Unlike _MBOX_ALIAS, these
# include inbox/sent/drafts — folders you can read but should not be a move
# *target* for inbound cleanup.
_MBOX_SOURCE_ALIAS = {
    **{k: v for k, v in _MBOX_ALIAS.items()},
    "inbox": "inbox", "收件箱": "inbox", "收件匣": "inbox",
    "sent": "sent", "sent mail": "sent", "已发送邮件": "sent",
    "已发邮件": "sent", "已发送": "sent",
    "drafts": "drafts", "draft": "drafts", "草稿": "drafts", "草稿箱": "drafts",
}
_MBOX_CATEGORY_NAMES = {
    "inbox": _MBOX_INBOX_NAMES,
    "junk": _MBOX_JUNK_NAMES,
    "trash": _MBOX_TRASH_NAMES,
    "archive": _MBOX_ARCHIVE_NAMES,
    "sent": _MBOX_SENT_NAMES,
    "drafts": _MBOX_DRAFTS_NAMES,
}


def _mbox_candidates(name: str) -> list[str]:
    """Resolve a mailbox name/alias to the list of locale folder names to match.

    Maps inbox/junk/trash/archive/sent/drafts (in English or Chinese) to their
    per-locale candidate names; falls back to the literal name for custom
    folders. Lets every mail function accept 'Sent'/'Junk'/'已发送邮件' etc.
    interchangeably regardless of the account's display language.
    """
    cat = _MBOX_SOURCE_ALIAS.get(name.strip().lower())
    if cat:
        return _MBOX_CATEGORY_NAMES[cat]
    return [name]


# AppleScript snippet: formats `msgDate` → `dateStr` (YYYY-MM-DD HH:MM)
_MAIL_FMT_DATE = (
    'set _y to year of msgDate as string\n'
    'set _mo to (month of msgDate as integer)\n'
    'if _mo < 10 then set _mo to "0" & _mo\n'
    'set _dy to day of msgDate\n'
    'if _dy < 10 then set _dy to "0" & _dy\n'
    'set _t to time of msgDate\n'
    'set _h to _t div 3600\n'
    'set _mi to (_t mod 3600) div 60\n'
    'if _h < 10 then set _h to "0" & _h\n'
    'if _mi < 10 then set _mi to "0" & _mi\n'
    'set dateStr to _y & "-" & _mo & "-" & _dy & " " & _h & ":" & _mi\n'
)


def list_accounts() -> str:
    """List all email accounts configured in macOS Mail.app."""
    script = """
tell application "Mail"
    set output to ""
    set i to 0
    repeat with acc in every account
        set i to i + 1
        set accName to full name of acc
        set accEmail to ""
        try
            set addrs to email addresses of acc
            if (count of addrs) > 0 then set accEmail to item 1 of addrs
        end try
        if accEmail is "" then
            try
                set accEmail to user name of acc
            on error
                set accEmail to "(unknown)"
            end try
        end if
        set output to output & i & ". " & accName & " <" & accEmail & ">\\n"
    end repeat
    if output is "" then return "no accounts configured in Mail.app"
    return output
end tell
"""
    return _osascript_clean(script, timeout=15)


def _mail_mbox_find_script(mailbox: str, account: str = "") -> str:
    """Return AppleScript snippet that sets `foundMboxes` to matching mailboxes.

    Uses ``every account`` → ``mailboxes of acc`` because:
    - ``inbox of account`` is broken on macOS 26 (even for imap account subtypes)
    - ``every mailbox`` at app level only returns system mailboxes (Outbox, Drafts)
    - ``mailboxes of acc`` reliably lists all per-account mailboxes

    The mailbox name/alias is resolved to locale candidate names via
    _mbox_candidates, so 'INBOX'/'Sent'/'Junk'/'已发送邮件' all match regardless
    of the account's display language. One mailbox per account is collected
    (exit repeat after first match).
    """
    cands = _mbox_candidates(mailbox)
    checks = " or ".join(
        f'mbName is "{c.replace(chr(34), chr(92) + chr(34))}"' for c in cands
    )
    name_check = checks if checks else "false"

    if account:
        a = account.replace('"', '\\"')
        inner = f"""
        if accUser is "{a}" then
            try
                repeat with mb in (mailboxes of acc)
                    set mbName to name of mb
                    if {name_check} then
                        set end of foundMboxes to mb
                        exit repeat
                    end if
                end repeat
            end try
        end if
"""
    else:
        inner = f"""
        try
            repeat with mb in (mailboxes of acc)
                set mbName to name of mb
                if {name_check} then
                    set end of foundMboxes to mb
                    exit repeat
                end if
            end repeat
        end try
"""

    return f"""
set foundMboxes to {{}}
repeat with acc in (every account)
    set accUser to ""
    try
        set accUser to user name of acc
    end try
    {inner}
end repeat
"""


def list_mail(account: str = "", mailbox: str = "INBOX",
             limit: int = 20, unread_only: bool = False) -> str:
    """List emails from macOS Mail.app across all (or a specific) account."""
    mbox_find = _mail_mbox_find_script(mailbox, account)
    msgs_stmt = (
        "set msgs to (messages of theMbox whose read status is false)"
        if unread_only else
        "set msgs to messages of theMbox"
    )
    unread_tag = " unread" if unread_only else ""
    mbox_label = mailbox.replace('"', '\\"')

    script = f"""
tell application "Mail"
    set output to ""
    set counter to 0
    {mbox_find}
    if (count of foundMboxes) = 0 then
        return "no mailbox named '{mbox_label}' found — check account sync"
    end if
    repeat with theMbox in foundMboxes
        try
            set accLabel to ""
            try
                set accLabel to user name of (account of theMbox)
            end try
            if accLabel is "" then set accLabel to name of theMbox
            {msgs_stmt}
            set msgCount to count of msgs
            if msgCount > 0 then
                set output to output & "[" & accLabel & "] " & msgCount & "{unread_tag} message(s)" & "\\n"
                repeat with i from 1 to msgCount
                    if counter >= {limit} then exit repeat
                    set msg to item i of msgs
                    set msgSubject to subject of msg
                    set msgSender to sender of msg
                    set msgRead to read status of msg
                    set msgDate to date received of msg
                    set msgId to ""
                    try
                        set msgId to message id of msg
                    end try
                    {_MAIL_FMT_DATE}
                    if msgRead then
                        set mark to "  "
                    else
                        set mark to "● "
                    end if
                    set output to output & mark & msgSender & " | " & msgSubject & " | " & dateStr & " | id:" & msgId & "\\n"
                    set counter to counter + 1
                end repeat
                set output to output & "\\n"
            end if
        on error errMsg
            set output to output & "[error] " & errMsg & "\\n"
        end try
    end repeat
    if output is "" then return "no messages found"
    return output
end tell
"""
    return _osascript_clean(script, timeout=60)


def read_mail(subject: str = "", sender: str = "", account: str = "",
             mailbox: str = "INBOX", message_id: str = "") -> str:
    """Read the full content of an email from macOS Mail.app.

    Locate by `message_id` (preferred, from list_mail output) or by subject.
    """
    where = _mail_where(subject, sender, message_id)
    s_escaped = (message_id or subject).replace('"', '\\"')
    mbox_find = _mail_mbox_find_script(mailbox, account)

    script = f"""
tell application "Mail"
    {mbox_find}
    if (count of foundMboxes) = 0 then
        return "error: mailbox not found"
    end if
    repeat with theMbox in foundMboxes
        try
            set msgs to (messages of theMbox {where})
            if (count of msgs) > 0 then
                set msg to item 1 of msgs
                set msgFrom to sender of msg
                set msgSubject to subject of msg
                set msgDate to date received of msg
                set msgContent to content of msg
                {_MAIL_FMT_DATE}
                if (length of msgContent) > 3000 then
                    set msgContent to (text 1 thru 3000 of msgContent) & "\\n...[truncated]"
                end if
                return "From: " & msgFrom & "\\nDate: " & dateStr & "\\nSubject: " & msgSubject & "\\n---\\n" & msgContent
            end if
        end try
    end repeat
    return "error: no message found matching \\"{s_escaped}\\""
end tell
"""
    return _osascript_clean(script, timeout=30)
