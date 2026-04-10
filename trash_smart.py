import subprocess, sys
subprocess.check_call([sys.executable, "-m", "pip", "install", "-q",
    "google-api-python-client", "google-auth-httplib2", "google-auth-oauthlib"])

import json, os
from datetime import datetime, timedelta
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES           = ["https://www.googleapis.com/auth/gmail.modify"]
TOKEN_FILE       = "token.json"
CREDENTIALS_FILE = "credentials.json"
TRASH_LOG        = "trashed_smart.json"

# Security alerts older than this many days get trashed
SECURITY_ALERT_MAX_AGE_DAYS = 365

# ── Queries ───────────────────────────────────────────────────────────────────
# Gmail category labels cover promotions, newsletters, automated updates, etc.
# Security alerts are trashed only if older than 1 year (before cutoff date).

cutoff = (datetime.today() - timedelta(days=SECURITY_ALERT_MAX_AGE_DAYS)).strftime("%Y/%m/%d")

QUERIES = {
    "Promotions (Gmail category)":
        "category:promotions",
    "Updates / automated (Gmail category)":
        "category:updates",
    "Forums / newsletters (Gmail category)":
        "category:forums",
    f"Security alerts older than 1 year (before {cutoff})": (
        f"before:{cutoff} ("
        "subject:\"security alert\" OR subject:\"sign-in attempt\" OR "
        "subject:\"new sign-in\" OR subject:\"new device\" OR "
        "subject:\"unusual activity\" OR subject:\"unusual sign-in\" OR "
        "subject:\"suspicious activity\" OR subject:\"verify your\" OR "
        "subject:\"password reset\" OR subject:\"account recovery\" OR "
        "subject:\"2-step verification\" OR subject:\"two-factor\""
        ")"
    ),
}

# ── Auth ──────────────────────────────────────────────────────────────────────

def get_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return build("gmail", "v1", credentials=creds)

# ── Paginate a query to exhaustion ───────────────────────────────────────────

def collect_ids(service, query, label):
    ids = set()
    page_token = None
    while True:
        kwargs = {"userId": "me", "maxResults": 500, "q": query}
        if page_token:
            kwargs["pageToken"] = page_token
        resp = service.users().messages().list(**kwargs).execute()
        msgs = resp.get("messages", [])
        if not msgs:
            break
        ids.update(m["id"] for m in msgs)
        print(f"  [{label}] {len(ids)} IDs so far...", flush=True)
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return ids

# ── Batch trash ───────────────────────────────────────────────────────────────

def batch_trash(service, ids, label):
    ids = list(ids)
    total = len(ids)
    if total == 0:
        print(f"  Nothing to trash for: {label}")
        return 0
    done = 0
    for i in range(0, total, 1000):
        chunk = ids[i:i+1000]
        service.users().messages().batchModify(
            userId="me",
            body={"ids": chunk, "addLabelIds": ["TRASH"], "removeLabelIds": ["INBOX"]},
        ).execute()
        done += len(chunk)
        print(f"  Trashed {done}/{total}...", flush=True)
    return total

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("Authenticating...")
    service = get_service()
    print()

    all_ids   = set()
    log       = {}

    # 1. Collect IDs for each query
    for label, query in QUERIES.items():
        print(f"Querying: {label}")
        ids = collect_ids(service, query, label)
        new_ids = ids - all_ids
        print(f"  → {len(ids)} total ({len(new_ids)} new after dedup)\n")
        log[label] = list(new_ids)
        all_ids.update(ids)

    print(f"Total unique IDs to trash: {len(all_ids)}\n")

    # Save log before touching anything
    with open(TRASH_LOG, "w") as f:
        json.dump({"total": len(all_ids), "by_category": {k: len(v) for k, v in log.items()}}, f, indent=2)
    print(f"Trash log saved to {TRASH_LOG}\n")

    # 2. Trash everything in one combined batch pass
    print(f"Moving {len(all_ids)} emails to trash in batches of 1000...")
    total_trashed = batch_trash(service, all_ids, "all")

    print(f"\nDone. {total_trashed} emails moved to trash.")
    print("\nBreakdown:")
    for label, ids in log.items():
        print(f"  {len(ids):>6}  {label}")

if __name__ == "__main__":
    main()
