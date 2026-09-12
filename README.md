# AI Email & Calendar Assistant

An autonomous agent that monitors a Gmail inbox, classifies incoming emails with Azure OpenAI (GPT-4o), and takes appropriate actions including calendar coordination with human-in-the-loop approval.

## What it does

- **Monitors** a Gmail inbox continuously (configurable poll interval)
- **Classifies** every email into one of six labels using GPT-4o:
  - `MEETING_REQUEST` — triggers calendar availability check + manager approval flow
  - `TASK` — action item for the manager; labelled and logged
  - `INFO_ONLY` — FYI / no action needed; labelled and logged
  - `SALES_OUTREACH` — unsolicited vendor/sales contact
  - `MARKETING` — newsletters, promos, bulk mail
  - `SPAM` — moved to spam folder
  - `URGENT` modifier — added to any label when a deadline or escalation is detected
- **Meeting flow**: finds free calendar slots → emails manager for `APPROVE N` / `REJECT` → on approval creates the event and replies to the sender
- **Full audit trail** in SQLite (`/data/agent.db`)

## Architecture

```
auth.py                       one-time OAuth setup script (run locally, not in Docker)

agent/
├── main.py                   polling loop, graceful shutdown, JSON logging
├── config.py                 all config via env vars, fail-fast validate()
│
├── google/
│   ├── auth.py               OAuth token load + atomic refresh
│   ├── gmail_client.py       read, label, archive, flag, send via Gmail API
│   └── calendar_client.py    find free slots (respects existing events), create events
│
├── ai/
│   └── classifier.py         GPT-4o classification → label, urgency, auto_reply, is_invoice
│
├── workflows/
│   ├── orchestrator.py       classify → label → action dispatcher per email
│   └── approval_flow.py      approval email → APPROVE/REJECT → calendar event + confirm
│
├── storage/
│   ├── database.py           SQLite: processed emails, pending approvals, audit log
│   └── audit.py              CLI audit report (python -m agent.storage.audit)
│
└── templates/
    ├── approval_request.txt  manager approval email body
    ├── meeting_confirmed.txt reply to original sender on approval
    └── urgent_notification.txt manager alert for URGENT+TASK emails
```

## Prerequisites

1. A Google Cloud project with the **Gmail API** and **Google Calendar API** enabled
2. An **OAuth 2.0 Client ID** of type *Desktop app* — download as `credentials.json`
3. **Azure OpenAI** access with a `gpt-4o` deployment
4. Docker & Docker Compose

## Setup

### 1 — Place Google credentials

```
email_demo/
└── credentials/
    └── credentials.json   ← downloaded from Google Cloud Console
```

### 2 — Authenticate with Google (once, on your local machine)

```bash
cd email_demo
pip install google-auth-oauthlib
CREDENTIALS_PATH=credentials/credentials.json \
TOKEN_PATH=credentials/token.json \
python auth.py
```

This opens a browser tab for OAuth consent. After approval, `credentials/token.json` is saved automatically.

### 3 — Configure environment

```bash
cp .env.example .env
```

Edit `.env` and fill in the required values:

| Variable | Required | Description |
|---|---|---|
| `AZURE_OPENAI_API_KEY` | ✓ | Azure OpenAI API key |
| `AZURE_OPENAI_ENDPOINT` | ✓ | e.g. `https://your-resource.openai.azure.com` |
| `AZURE_OPENAI_DEPLOYMENT` | ✓ | Deployment name, e.g. `gpt-4o` |
| `AZURE_OPENAI_API_VERSION` | ✓ | e.g. `2024-12-01-preview` |
| `MANAGER_EMAIL` | ✓ | Who receives and approves meeting requests |
| `MONITORED_EMAIL` | — | Informational — whose inbox is being watched |

### 4 — Start the agent

```bash
docker compose up --build
```

Logs stream to stdout. The agent processes all unread inbox messages on first start, then polls on the configured interval (default 60 s).

## How the approval flow works

1. A `MEETING_REQUEST` email arrives → agent checks calendar and emails the manager:

```
Subject: [APPROVAL REQUEST] Meeting request from ... — original subject

  [1] Monday, Sep 15, 2026 10:00 AM – 11:00 AM UTC
  [2] Tuesday, Sep 16, 2026 02:00 PM – 03:00 PM UTC
  [3] Wednesday, Sep 17, 2026 11:00 AM – 12:00 PM UTC

Reply with:  APPROVE 1   or   REJECT
```

2. Manager replies **`APPROVE 2`** → the agent creates a Google Calendar event at slot 2 and sends a confirmation to the original sender.

3. Manager replies **`REJECT`** → the decision is logged; no further action.

## Audit & monitoring

```bash
# Human-readable report (last 24h)
DB_PATH=data/agent.db python -m agent.storage.audit

# Extend the window
DB_PATH=data/agent.db python -m agent.storage.audit --hours 48

# Raw SQL
sqlite3 data/agent.db "SELECT timestamp, event_type, details FROM audit_log ORDER BY id DESC LIMIT 20;"
```

## Running locally (without Docker)

```bash
cd email_demo
pip install -r requirements.txt
cp .env.example .env
# Override Docker paths in .env:
#   CREDENTIALS_PATH=credentials/credentials.json
#   TOKEN_PATH=credentials/token.json
#   DB_PATH=data/agent.db
python -m agent.main
```

## Running tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

## Troubleshooting

| Problem | Fix |
|---|---|
| `token.json not found` | Run `python auth.py` to re-authenticate |
| `RefreshError` on startup | Token revoked — run `python auth.py` again |
| Stuck `PENDING` approval | `sqlite3 data/agent.db "UPDATE pending_approvals SET status='REJECTED' WHERE id='<id>'"` |
| Agent not picking up emails | Check `LOG_LEVEL=DEBUG` and verify `MONITORED_EMAIL` is the correct inbox |
| Slot times look wrong | `WORKING_HOURS_START`/`END` are interpreted as **UTC hours** |

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `AZURE_OPENAI_API_KEY` | — | Required |
| `AZURE_OPENAI_ENDPOINT` | — | Required |
| `AZURE_OPENAI_DEPLOYMENT` | `gpt-4o` | Model deployment name |
| `AZURE_OPENAI_API_VERSION` | `2024-12-01-preview` | API version |
| `MANAGER_EMAIL` | — | Required: who approves meetings |
| `MONITORED_EMAIL` | — | Informational |
| `POLL_INTERVAL` | `60` | Seconds between inbox polls |
| `CALENDAR_ID` | `primary` | Google Calendar ID |
| `MEETING_LOOKAHEAD_DAYS` | `5` | Business days ahead to search for slots |
| `MEETING_SLOT_COUNT` | `3` | Slot options to offer manager |
| `WORKING_HOURS_START` | `9` | UTC hour — start of bookable day |
| `WORKING_HOURS_END` | `17` | UTC hour — end of bookable day |
| `DEFAULT_MEETING_DURATION` | `60` | Minutes, when email doesn't specify |
| `SLOT_STEP_MINUTES` | `30` | Free-slot search granularity |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `LOG_FORMAT` | `text` | `text` (human-readable) or `json` (for log aggregators) |
| `AUTO_DECLINE_SALES` | `true` | Set to `false` to suppress polite decline replies to sales emails |
| `API_RETRIES` | `5` | Google API retry count on 429/5xx responses |
