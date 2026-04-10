import subprocess, sys
subprocess.check_call([sys.executable, "-m", "pip", "install", "-q",
    "google-api-python-client", "google-auth-httplib2", "google-auth-oauthlib"])

import json, os, re
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

SCOPES           = ["https://www.googleapis.com/auth/gmail.modify"]
TOKEN_FILE       = "token.json"
CREDENTIALS_FILE = "credentials.json"
EMAILS_FILE      = "emails.json"
TRASH_LOG        = "trashed_ids.json"

# ── Classification heuristics ─────────────────────────────────────────────────

FROM_PATTERNS = re.compile(
    r"noreply|no.reply|do.not.reply|donotreply|"
    r"newsletter|newsletters|"
    r"promo|promotions?|marketing|"
    r"offers?|deals?|savings?|coupons?|"
    r"alerts?|notifications?|"
    r"digest|mailer|mailings?|"
    r"unsubscribe|"
    r"@em\.|@e\.|@mail\.|@email\.|@messages?\.|"
    r"@mystore\.|@oe\.|@info\.|@news\.",
    re.IGNORECASE,
)

SUBJECT_PATTERNS = re.compile(
    r"\d+\s*%\s*off|"
    r"\bsale\b|\bflash sale\b|\bclearance\b|"
    r"\bdeal\b|\bdeals\b|\bdealsof\b|"
    r"\boffer\b|\bexclusive offer\b|\bspecial offer\b|"
    r"\bdiscount\b|\bcoupon\b|\bpromo code\b|\bvoucher\b|"
    r"\bunsubscribe\b|\bnewsletter\b|"
    r"\bfree shipping\b|\bfree trial\b|"
    r"\blimited time\b|\bact now\b|\bdon.t miss\b|"
    r"\bpoints?\b.*\breward|\breward.*\bpoints?\b|"
    r"\bweekly digest\b|\bmonthly digest\b|\bweekly update\b|"
    r"\byour .* statement\b|\bstatement ready\b|"
    r"\byour order\b|\border confirmation\b|\bshipment\b|\btracking\b",
    re.IGNORECASE,
)

# Sender domains/addresses that are always bulk
BULK_SENDER_DOMAINS = {
    "ulta.com", "allbirds.com", "acdsystems.net", "shutterfly.com",
    "cvs.com", "jcrew.com", "spotify.com", "target.com", "lowesprotectionplus.com",
    "mail.billiondollarsellers.com", "f6s.com", "substack.com",
    "jetpens.com", "e.ulta.com", "em.shutterfly.com", "mail.allbirds.com",
    "mail.jcrew.com",
}

def extract_email_domain(from_field):
    m = re.search(r"@([\w.\-]+)", from_field)
    return m.group(1).lower() if m else ""

def is_bulk(email):
    frm     = email.get("from", "")
    subject = email.get("subject", "")
    domain  = extract_email_domain(frm)

    if domain in BULK_SENDER_DOMAINS:
        return True
    if FROM_PATTERNS.search(frm):
        return True
    if SUBJECT_PATTERNS.search(subject):
        return True
    return False

# ── Gmail auth ────────────────────────────────────────────────────────────────

def get_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            from google_auth_oauthlib.flow import InstalledAppFlow
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return build("gmail", "v1", credentials=creds)

# ── Batch trash (1000 per call) ───────────────────────────────────────────────

def batch_trash(service, msg_ids):
    total = len(msg_ids)
    trashed = 0
    for i in range(0, total, 1000):
        chunk = msg_ids[i:i+1000]
        service.users().messages().batchModify(
            userId="me",
            body={"ids": chunk, "addLabelIds": ["TRASH"], "removeLabelIds": ["INBOX"]},
        ).execute()
        trashed += len(chunk)
        print(f"  Trashed {trashed}/{total}...", flush=True)

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    with open(EMAILS_FILE, encoding="utf-8") as f:
        emails = json.load(f)

    bulk = [e for e in emails if is_bulk(e)]
    ids  = [e["message_id"] for e in bulk]

    print(f"Classified {len(ids)}/{len(emails)} emails as newsletters / promos / alerts.")

    # Save log of what will be trashed
    with open(TRASH_LOG, "w", encoding="utf-8") as f:
        json.dump([{"message_id": e["message_id"], "from": e["from"], "subject": e["subject"]}
                   for e in bulk], f, ensure_ascii=False, indent=2)
    print(f"Trash log saved to {TRASH_LOG}\n")

    print("Authenticating...")
    service = get_service()

    print(f"Moving {len(ids)} emails to trash in batches of 1000...")
    batch_trash(service, ids)

    print(f"\nDone. {len(ids)} emails moved to trash.")

if __name__ == "__main__":
    main()
