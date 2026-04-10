# Gmail Cleanup

A set of Python scripts for bulk-cleaning a Gmail inbox and triaging unreplied emails from macOS Mail.

## Scripts

### `fetch_emails.py`
Fetches email metadata (sender, subject, date) from Gmail via the API and saves it to `emails.json`. Uses parallel threads for fast retrieval.

- Configurable cap (`MAX_EMAILS`), date range (`DATE_AFTER` / `DATE_BEFORE`), and thread count (`WORKERS`)
- Output: `emails.json`

### `trash_bulk.py`
Classifies emails in `emails.json` as bulk/promotional using sender patterns and subject heuristics, then moves them to trash in batches of 1000.

- Hardcoded blocklist of known bulk sender domains
- Saves a log of trashed messages to `trashed_ids.json` before taking action
- Requires `emails.json` from `fetch_emails.py`

### `trash_smart.py`
Uses Gmail's native category labels to trash promotions, updates, forums, and old security alerts directly via API queries — no local `emails.json` needed.

- Targets `category:promotions`, `category:updates`, `category:forums`
- Trashes security alert emails (password resets, sign-in notices, etc.) older than 1 year
- Saves a summary log to `trashed_smart.json`

### `triage_mail.py`
Reads directly from macOS Mail's local SQLite database and `.emlx` files to find unreplied emails from the last 100 days, filtering out junk, and writes a Markdown triage report.

- No AppleScript or Automation permission needed — requires **Full Disk Access** for Terminal
- Filters by the "answered" flag in Mail's database
- Output: `needs_reply_summary.md`

## Setup

### 1. Google API credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/) and create a project
2. Enable the **Gmail API**
3. Create OAuth 2.0 credentials (Desktop app) and download as `credentials.json` into this directory
4. On first run, a browser window will open to authorize access; the token is saved to `token.json`

### 2. Install dependencies

```bash
pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib
```

Or just run any script — they auto-install their dependencies on startup.

### 3. For `triage_mail.py`

Grant **Full Disk Access** to Terminal (or your IDE) in System Settings → Privacy & Security → Full Disk Access.

The script has hardcoded UUIDs for a specific Mail account and inbox. Edit the constants near the top of the file to match your setup:

```python
ACCOUNT_UUID = "..."   # find in ~/Library/Mail/V10/
MBOX_UUID    = "..."   # subfolder inside your Inbox.mbox
INBOX_ROWID  = 6       # run a quick SQLite query to find yours
```

## Typical workflow

```bash
# Step 1: fetch all email metadata
python fetch_emails.py

# Step 2: trash obvious bulk mail based on heuristics
python trash_bulk.py

# Step 3: trash remaining promotions/updates/forums via Gmail categories
python trash_smart.py

# Step 4: generate a triage report of unreplied emails (macOS Mail only)
python triage_mail.py
```

## Security notes

- `credentials.json` and `token.json` are excluded from this repo via `.gitignore` — never commit them
- `trash_bulk.py` and `trash_smart.py` move emails to **Trash**, not permanent deletion — you have 30 days to recover anything moved in error
