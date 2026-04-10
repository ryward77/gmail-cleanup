#!/usr/bin/env python3
"""
macOS Mail Triage Script — READ-ONLY
Reads directly from Mail's local SQLite cache and .emlx files.
No AppleScript / Automation permission needed (requires Full Disk Access).

Account: rxyan2@wm.edu  |  Inbox  |  Last 100 days  |  Unreplied only
Output:  needs_reply_summary.md
"""

from __future__ import annotations

import sqlite3
import email
import email.policy
import email.header
import re
import html
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── Hard-coded paths (discovered from this machine) ─────────────────────────
MAIL_ROOT   = Path.home() / "Library/Mail/V10"
ACCOUNT_UUID = "EBC0FD63-6D0A-4344-BA27-78BC996907F7"   # rxyan2@wm.edu account
MBOX_UUID    = "8C7E7F39-E99B-4C1C-8A07-190101E89282"   # Inbox sub-UUID
INBOX_ROWID  = 6                                          # mailboxes.ROWID for Inbox
DB_PATH      = MAIL_ROOT / "MailData/Envelope Index"

INBOX_DATA = (
    MAIL_ROOT / ACCOUNT_UUID
    / "Inbox.mbox" / MBOX_UUID / "Data"
)

ANSWERED_FLAG = 1 << 2   # bit 2 of flags = "was replied to"
DELETED_FLAG  = 1 << 1   # bit 1 = deleted

SCRIPT_DIR = Path(__file__).parent
OUT_PATH   = SCRIPT_DIR / "needs_reply_summary.md"

# ── emlx path formula ────────────────────────────────────────────────────────

def emlx_path(rowid: int) -> Optional[Path]:
    """Compute the local path to a message's .emlx file."""
    d1 = (rowid // 1000) % 10
    d2 = (rowid // 10_000) % 10
    d3 = (rowid // 100_000) % 10

    if d3 > 0:
        candidates = [
            INBOX_DATA / str(d1) / str(d2) / str(d3) / "Messages" / f"{rowid}.emlx",
            INBOX_DATA / str(d1) / str(d2) / str(d3) / "Messages" / f"{rowid}.partial.emlx",
        ]
    elif d2 > 0:
        candidates = [
            INBOX_DATA / str(d1) / str(d2) / "Messages" / f"{rowid}.emlx",
            INBOX_DATA / str(d1) / str(d2) / "Messages" / f"{rowid}.partial.emlx",
        ]
    else:
        candidates = [
            INBOX_DATA / str(d1) / "Messages" / f"{rowid}.emlx",
            INBOX_DATA / str(d1) / "Messages" / f"{rowid}.partial.emlx",
            INBOX_DATA / "Messages" / f"{rowid}.emlx",
            INBOX_DATA / "Messages" / f"{rowid}.partial.emlx",
        ]

    for p in candidates:
        if p.exists():
            return p
    return None


# ── Parse .emlx → Python email.message ──────────────────────────────────────

def parse_emlx(path: Path) -> Optional[email.message.Message]:
    """
    An .emlx file is:  <byte-count>\n<raw RFC-2822 message>\n<?xml ...plist...>
    We strip the leading byte count and the trailing plist.
    """
    try:
        raw = path.read_bytes()
    except (PermissionError, OSError):
        return None

    # Find the first newline — that's the byte-count line
    nl = raw.find(b"\n")
    if nl < 0:
        return None
    raw = raw[nl + 1:]

    # Strip trailing Apple plist (starts with <?xml or <\xff\xfe?xml in UTF-16)
    xml_marker = raw.find(b"<?xml")
    if xml_marker > 0:
        raw = raw[:xml_marker]

    try:
        return email.message_from_bytes(raw, policy=email.policy.compat32)
    except Exception:
        return None


def decode_header_value(value: str) -> str:
    """Decode RFC 2047 encoded-word sequences in header values."""
    if not value:
        return ""
    parts = email.header.decode_header(value)
    decoded = []
    for part, enc in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return "".join(decoded)


def get_text_body(msg: email.message.Message) -> str:
    """Extract plain-text body, falling back to HTML → stripped."""
    plain, html_body = [], []

    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cte = (part.get("Content-Transfer-Encoding") or "").lower()
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace")
            if ct == "text/plain":
                plain.append(text)
            elif ct == "text/html":
                html_body.append(text)
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace")
            ct = msg.get_content_type()
            if ct == "text/html":
                html_body.append(text)
            else:
                plain.append(text)

    if plain:
        return "\n".join(plain)
    if html_body:
        # Very basic HTML → text
        t = "\n".join(html_body)
        t = re.sub(r"<br\s*/?>", "\n", t, flags=re.IGNORECASE)
        t = re.sub(r"<[^>]+>", " ", t)
        return html.unescape(t)
    return ""


def clean_body(text: str) -> str:
    """Strip quoted lines, collapse whitespace, cap at 700 chars."""
    lines = text.splitlines()
    out = []
    for line in lines:
        s = line.strip()
        if s.startswith(">"):
            continue
        if re.match(r"^On .{5,80} wrote:$", s):
            break
        out.append(s)
    result = re.sub(r"\s{2,}", " ", " ".join(out)).strip()
    return result[:700]


# ── Junk filter ──────────────────────────────────────────────────────────────

JUNK_ADDR = re.compile(
    r"no[_\-.]?reply|do[_\-.]?not[_\-.]?reply|noreply|"
    r"newsletter|notifications?@|alerts?@|updates?@|"
    r"mailer[_\-.]?daemon|postmaster|bounce@|"
    r"support@|help@|info@|sales@|marketing@|"
    r"subscriptions?@|digest@|announce@",
    re.IGNORECASE,
)
JUNK_SUBJ = re.compile(
    r"unsubscribe|newsletter|digest|"
    r"out of office|automatic.?reply|auto[_\-.]?reply|"
    r"delivery (status|notification|failure)|returned mail|"
    r"invitation to (connect|join)|"
    r"\b(promo|offer|deal|sale|discount|coupon|% off|free trial)\b|"
    r"verify your email|confirm your (email|account)|"
    r"your (order|receipt|invoice|subscription)|"
    r"weekly (digest|roundup|summary)|you have \d+ new",
    re.IGNORECASE,
)
JUNK_BODY = re.compile(
    r"to unsubscribe|manage your (preferences|subscription)|"
    r"you are receiving this (email|because)|"
    r"this is an automated (email|message|notification)|"
    r"do not reply to this email",
    re.IGNORECASE,
)


def is_junk(sender_email: str, subject: str, body: str = "") -> bool:
    return bool(
        JUNK_ADDR.search(sender_email)
        or JUNK_SUBJ.search(subject)
        or (body and JUNK_BODY.search(body))
    )


# ── Parse sender string ──────────────────────────────────────────────────────

def parse_sender(raw: str):
    m = re.match(r'^"?([^"<]+?)"?\s*<([^>]+)>', raw.strip())
    if m:
        return m.group(1).strip(), m.group(2).strip().lower()
    return "", raw.strip().lower()


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    cutoff_ts = int(time.time()) - 100 * 86400

    # ── Step 1: SQL query — metadata only, very fast ─────────────────────────
    print("Querying Mail database…")
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    rows = con.execute("""
        SELECT
            m.ROWID        AS rowid,
            m.flags        AS flags,
            m.date_received AS date_received,
            ad.comment     AS sender_display,
            ad.address     AS sender_email,
            s.subject      AS subject
        FROM messages m
        JOIN subjects  s  ON m.subject = s.ROWID
        LEFT JOIN addresses ad ON m.sender = ad.ROWID
        WHERE m.mailbox        = ?
          AND m.date_received >= ?
          AND m.deleted        = 0
          AND (m.flags & ?)    = 0
        ORDER BY m.date_received DESC
    """, (INBOX_ROWID, cutoff_ts, ANSWERED_FLAG)).fetchall()
    con.close()

    print(f"  Unreplied messages (last 100 days): {len(rows)}")

    # ── Step 2: metadata-only junk filter ────────────────────────────────────
    kept = []
    for r in rows:
        subj  = r["subject"] or ""
        email_addr = (r["sender_email"] or "").lower()
        if not is_junk(email_addr, subj):
            kept.append(r)

    print(f"  After metadata junk filter: {len(kept)}")

    if not kept:
        print("Nothing actionable — you're all caught up!")
        return

    # ── Step 3: read emlx bodies ─────────────────────────────────────────────
    print(f"Reading .emlx files for {len(kept)} message(s)…")
    results = []
    missing_body = 0

    for r in kept:
        rowid      = r["rowid"]
        subj       = decode_header_value(r["subject"] or "")
        raw_sender = f'{r["sender_display"] or ""} <{r["sender_email"] or ""}>'
        s_name, s_email = parse_sender(raw_sender)
        ts = r["date_received"]
        date_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")

        body_preview = ""
        p = emlx_path(rowid)
        if p:
            msg = parse_emlx(p)
            if msg:
                raw_body = get_text_body(msg)
                body_preview = clean_body(raw_body)
        else:
            missing_body += 1

        # Body-level junk check
        if is_junk(s_email, subj, body_preview):
            continue

        results.append({
            "sender_name":  s_name or s_email,
            "sender_email": s_email,
            "subject":      subj,
            "date":         date_str,
            "body_preview": body_preview,
        })

    print(f"  emlx files not found locally: {missing_body}")
    print(f"  Final actionable emails: {len(results)}")

    # ── Step 4: write Markdown ────────────────────────────────────────────────
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "# Email Triage — Needs Reply",
        "",
        f"Generated: {now}  ",
        f"Account: rxyan2@wm.edu  ",
        "Window: last 100 days  ",
        f"Total actionable: **{len(results)}**",
        "",
        "---",
        "",
    ]
    for i, e in enumerate(results, 1):
        lines += [
            f"## {i}. {e['subject'] or '(no subject)'}",
            "",
            f"- **From:** {e['sender_name']} `<{e['sender_email']}>`",
            f"- **Date:** {e['date']}",
            "",
            f"> {e['body_preview'] or '*(body not cached locally — open in Mail to download)*'}",
            "",
            "---",
            "",
        ]

    OUT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nSummary written → {OUT_PATH}")


if __name__ == "__main__":
    main()
