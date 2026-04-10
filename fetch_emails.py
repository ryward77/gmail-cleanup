import subprocess
import sys

# Ensure required packages are present in the current environment
packages = [
    "google-api-python-client",
    "google-auth-httplib2",
    "google-auth-oauthlib",
]
subprocess.check_call([sys.executable, "-m", "pip", "install", "-q"] + packages)

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"
OUTPUT_FILE = "emails.json"

# ── Fetch settings ────────────────────────────────────────────────────────────
MAX_EMAILS = 10_000          # set to None to fetch everything

# Optional date range (Gmail query format: YYYY/MM/DD).  Set to None to skip.
DATE_AFTER  = None           # e.g. "2023/01/01"  → emails after  Jan 1 2023
DATE_BEFORE = None           # e.g. "2024/01/01"  → emails before Jan 1 2024

WORKERS = 10                 # parallel threads for metadata fetching
# ─────────────────────────────────────────────────────────────────────────────


def build_query():
    parts = []
    if DATE_AFTER:
        parts.append(f"after:{DATE_AFTER}")
    if DATE_BEFORE:
        parts.append(f"before:{DATE_BEFORE}")
    return " ".join(parts) if parts else None


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
    return build("gmail", "v1", credentials=creds), creds


def list_all_message_ids(service, max_results, query):
    """Page through messages.list and collect all message IDs."""
    ids = []
    page_token = None
    query_info = f" (query: '{query}')" if query else ""
    print(f"Listing message IDs{query_info}...")

    while True:
        remaining = (max_results - len(ids)) if max_results else 500
        batch_size = min(500, remaining) if max_results else 500

        kwargs = {"userId": "me", "maxResults": batch_size}
        if query:
            kwargs["q"] = query
        if page_token:
            kwargs["pageToken"] = page_token

        response = service.users().messages().list(**kwargs).execute()
        messages = response.get("messages", [])
        if not messages:
            break

        ids.extend(m["id"] for m in messages)
        print(f"  Listed {len(ids)} IDs...", flush=True)

        if max_results and len(ids) >= max_results:
            ids = ids[:max_results]
            break

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return ids


def fetch_metadata(creds_json, msg_id):
    """Fetch metadata for a single message using a thread-local service."""
    creds = Credentials.from_authorized_user_info(json.loads(creds_json), SCOPES)
    svc = build("gmail", "v1", credentials=creds)
    msg_data = svc.users().messages().get(
        userId="me",
        id=msg_id,
        format="metadata",
        metadataHeaders=["From", "Subject", "Date"],
    ).execute()
    headers = {h["name"]: h["value"] for h in msg_data.get("payload", {}).get("headers", [])}
    return {
        "message_id": msg_id,
        "from":       headers.get("From", ""),
        "subject":    headers.get("Subject", ""),
        "date":       headers.get("Date", ""),
    }


def fetch_all_metadata(creds, msg_ids):
    """Fetch metadata for all IDs in parallel, each thread owns its service."""
    total = len(msg_ids)
    print(f"Fetching metadata for {total} emails using {WORKERS} threads...")
    creds_json = creds.to_json()
    emails = [None] * total
    completed = 0

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        future_to_idx = {
            executor.submit(fetch_metadata, creds_json, mid): i
            for i, mid in enumerate(msg_ids)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            emails[idx] = future.result()
            completed += 1
            if completed % 500 == 0 or completed == total:
                print(f"  Fetched metadata for {completed}/{total}...", flush=True)

    return emails


def main():
    print("Authenticating with Gmail API...")
    service, creds = get_service()

    query = build_query()
    msg_ids = list_all_message_ids(service, MAX_EMAILS, query)
    print(f"Found {len(msg_ids)} messages to fetch.\n")

    emails = fetch_all_metadata(creds, msg_ids)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(emails, f, ensure_ascii=False, indent=2)

    print(f"\nDone. {len(emails)} emails saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
