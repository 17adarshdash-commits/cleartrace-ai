# ClearTrace AI

An accessibility self-auditing agent.

Scans a page with axe-core, fixes what it can, re-scans to verify the
fix actually worked, and retries (capped at 3 attempts) on judgment-call
violations before flagging for human review.

Project Description: https://docs.google.com/document/d/1KCDtLeklnKNk2-JGJO9Xu7I8QXBl5YOF/edit

## What's real vs. what's a stub right now

- **Auditor (axe-core + Playwright)** — fully working, no stub.
- **Contrast fixer** — fully working, deterministic, no model call. Tested:
  `#777777` on `#ffffff` correctly fails (4.48:1) and gets fixed to `#767676` (4.54:1).
- **Alt-text / form-label agent** — the LLM call is wired but points at a
  placeholder model name (`REPLACE_ME` in `.env`). **This has not been run
  against a real model yet.** Confirm your actual API access, drop in real
  credentials, and test it against the demo site's two agentic violations
  before you trust it in a recorded demo.
- **Patcher** — fully working, but only reaches inline styles/attributes
  by design (see the docstring in `patcher.py` for why). The demo site is
  built to match that constraint.

## Setup

```bash
cd a11y-agent/backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt --break-system-packages
playwright install chromium
cp .env.example .env   # then fill in MODEL_API_URL / MODEL_API_KEY / MODEL_NAME
```

## Running it (three terminals)

**Terminal 1 — serve the demo site** (the backend patches
`demo-site-working/`, which mirrors `demo-site/` fresh on every run, so
point the static server at the *working* copy, not the source):

```bash
mkdir -p a11y-agent/demo-site-working
cp -r a11y-agent/demo-site/* a11y-agent/demo-site-working/
cd a11y-agent/demo-site-working
python3 -m http.server 8080
```

**Terminal 2 — run the backend:**

```bash
cd a11y-agent/backend
source venv/bin/activate
uvicorn main:app --reload --port 8000
```

**Terminal 3 — open the dashboard:**

```bash
cd a11y-agent/frontend
python3 -m http.server 5500
# open http://localhost:5500/dashboard.html
```

Click "Run Audit." It hits `POST /audit` on the backend, which resets
the working copy to a pristine state, scans, fixes each violation with
the right strategy (deterministic for contrast, agentic for alt-text
and labels), re-scans, and returns the full step log.

## Things to verify before you trust this for the recorded demo

1. **Model access** — confirm what's actually callable under your Codex
   hackathon allowance and whether it supports `response_format:
   json_object` (or adjust `llm_agent.py`'s parsing if not).
2. **Cold-start / latency** — if you deploy this (rather than run it
   locally for the demo), time the actual round-trip for a full audit
   cycle including Playwright browser launch. This was flagged earlier
   as a real risk on free-tier hosts.
3. **The "intentional failure" trap** — run the alt-text and label
   fixes several times locally and see where they *actually* struggle,
   rather than assuming the contrast violation (which is already a
   genuine borderline case at 4.48:1) is your only retry-demo moment.
4. **`_revalidate` currently hits a hardcoded `localhost:8080`** — fine
   for local dev, but if you deploy, this needs to point at the deployed
   demo site's actual served URL, not localhost.
