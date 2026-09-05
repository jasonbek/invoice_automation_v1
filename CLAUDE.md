# Invoice Automation — Claude Instructions

## Project Purpose
Travel agency invoice processing pipeline. Supplier PDFs come in via an n8n form, get
processed by a multi-agent Claude pipeline running on Modal.com, and results are returned
to n8n as separate JSON objects, each sent as a Telegram message (one per ClientBase screen).

## Stack
- **Python 3.11** — agent code
- **Modal.com** — serverless hosting (deploy once, pay per call)
- **Anthropic Claude API** (`claude-sonnet-4-6`) — all agent LLM calls
- **FastAPI** — HTTP endpoint inside Modal
- **n8n Cloud** — form input + Telegram output

## Key Commands
```bash
# Install Modal CLI locally (one time)
pip install modal

# Authenticate with Modal
modal setup

# Deploy the app manually (from project root) — normally NOT needed, see Deployment below
modal deploy app/main.py

# Run locally for testing (serves on localhost)
modal serve app/main.py

# Set the Anthropic API key as a Modal secret
modal secret create anthropic ANTHROPIC_API_KEY=sk-ant-...

# Syntax check all Python files (same check the CI workflow runs before deploying)
python -m compileall -q app
```

## Deployment
- **Auto-deploy is live**: pushing to `main` triggers `.github/workflows/deploy.yml`,
  which runs the syntax check above and then `modal deploy app/main.py` automatically.
  Commit + push to `main` is enough — no manual `modal deploy` needed for normal changes.
  If the syntax check fails, the deploy step is skipped (production is left untouched).
- This also picks up `docs/Commissions/*.md` (Air Canada / WestJet commission rate docs)
  since that folder is bundled into the container via `add_local_dir()` in `main.py` at
  deploy time — editing a commission doc and pushing to `main` makes the new rates live.
- CI auth: GitHub repo secrets `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` (Settings → Secrets
  and variables → Actions) authenticate the GitHub Actions runner to Modal. These are
  separate from the `anthropic` Modal secret below, which authenticates the running app
  to the Anthropic API — don't confuse the two when debugging deploy vs. runtime failures.
- Manual `modal deploy app/main.py` is only for out-of-band testing (e.g. deploying from
  a branch other than `main` before it's merged).
- The CI runner installs `requirements.txt` (not just the `modal` package) before
  deploying. `modal deploy` locally imports `app/main.py` to register the App object, and
  `main.py` imports `fastapi` at module level (which in turn pulls in `anthropic` via the
  extractor modules) — without those installed in the runner's env, the import fails
  before Modal ever gets to auth, and the deploy step fails fast with no useful Modal-side
  error. `image.pip_install(...)` inside `main.py` only installs packages *inside* the
  remote container; it does nothing for the CI machine doing the importing.

## Architecture (Pipeline)
```
n8n Form → POST /process-invoice (Modal)
  → Agent 1: markdown_agent.py   (PDF → clean Markdown)
  → Agent 2: routing_agent.py    (Markdown → {vendor, ruleSet, bookingTypes[]})
  → Agent 3+: extractors/ (parallel, one per booking type)
  → POST results to n8n webhook callback_url
n8n Webhook → Loop → Telegram (one message per section)
```

## File Structure
```
app/
├── main.py                          # Modal app definition + FastAPI ASGI endpoint
├── agents/
│   ├── markdown_agent.py            # Agent 1: PDF → Markdown
│   ├── routing_agent.py             # Agent 2: Markdown → routing JSON
│   └── extractors/
│       ├── __init__.py              # run_all() orchestrator
│       ├── base.py                  # Shared GLOBAL_RULES + call_claude()
│       ├── flight.py                # Flight schema + AC/WJ/ADX rules
│       ├── tour.py                  # Tour schema + Travel Brands/Viator rules
│       ├── hotel.py                 # Hotel schema
│       ├── cruise.py                # Cruise schema
│       ├── insurance.py             # Insurance schema (Manulife)
│       ├── service_fee.py           # Service fee (generated from form data)
│       └── new_traveller.py         # New traveller profile schema
agentmdv2.txt                        # Original monolithic instructions — source of truth
```

## Critical Business Rules (from agentmdv2.txt)
1. **Dates:** MUST be `MM/DD/YY` (e.g., "08/26/24") — no exceptions
2. **Times:** MUST be 12-hour with AM/PM (e.g., "4:40 PM")
3. **Missing fields:** Use `""` (empty string) — NEVER omit a key or use `null`, `undefined`, `"N/A"`
4. **Sections:** Each schema section = separate JSON object = separate Telegram message
5. **Non-CAD invoices (Tour/Cruise):** Must include `agentRemarks` with live conversion rate
6. **Commission:** Never calculate unless rules require; extract exact figure from invoice

## Vendor Routing Keys (`ruleSet`)
| ruleSet | Vendor |
|---|---|
| `air_canada` | Air Canada Internet |
| `westjet` | Westjet Internet |
| `vacation_package` | Air Canada Vacations / Westjet Vacations / Sunwing Vacations (packaged air + hotel) |
| `adx_intair` | ADX (has explicit COMMISSION line) |
| `expedia` | Expedia TAAP |
| `travel_brands` | Travel Brands / Intair (tours) |
| `viator` | Viator |
| `manulife` | Manulife Insurance |
| `generic` | All others |

## n8n Integration
**Input (POST to Modal):** `multipart/form-data`
- `vendor` (string)
- `booking_type_hint` (string, optional)
- `service_fee` (float, 0 if none)
- `callback_url` (string — n8n Webhook Trigger URL)
- `files[]` (one or more PDF or .md attachments)

**Output (Modal POSTs to callback_url):**
```json
{
  "status": "success",
  "sections": [
    { "sectionTitle": "Flight Screen 1 (Summary)", "data": { ... } },
    { "sectionTitle": "Flight Screen 2 (Segments)", "data": [ ... ] },
    ...
  ]
}
```

## Inbound Email (Resend Receiving)
`POST /inbound-email` lets an invoice be processed by forwarding the supplier email to
`invoices@invoicingtravel.beksautomate.me` (a dedicated receiving subdomain), instead of
using `/form`. Resend POSTs an `email.received` webhook (metadata only — sender, subject,
attachment list); `app/inbound.py` verifies the Svix-based webhook signature, then
fetches the actual body/attachment content via the Resend Receiving API and hands off to
the same `run_pipeline()` the manual upload uses. See `app/inbound.py` for the
fetch/verify logic and `app/main.py`'s `receive_inbound_email` / `dispatch_inbound_email`
for the route + dispatch split (the route responds immediately; attachment fetching
happens in the spawned function so the webhook ack isn't held up).

The route also checks the `to` address against `@invoicingtravel.beksautomate.me` and
ignores anything else (added 2026-09-05, see git history) — this Resend account also
receives inbound email for an unrelated project (`sds.beksautomate.me`), and that
project's mail was observed reaching this webhook too despite being a separate
subdomain. This filter is a defensive stopgap, not a fix for that cross-delivery — if it
resurfaces, check the Resend dashboard's Webhooks page for how many webhooks exist and
what each is actually scoped to.

## Invoice Automation 2.0 — mobile / ClientBase agent (Phase 1)
A separate, phone-driven agent that logs into ClientBase Online and fills in reservation
screens from this pipeline's JSON output — replacing the manual UI.Vision paste step —
lives in a sibling project, not this repo: `c:\Users\projectpc\clientbase-filer`
(private repo: `github.com/jasonbek/clientbase-filer`). It's a Claude Code session driven
via Remote Control, using the Browserbase MCP server for browser automation, with its own
`CLAUDE.md` (hard rules: screenshot/text-summary approval before every Save, human-only
login via a Browserbase Live View link, never touch this or that machine's login form)
and `NAVIGATION.md` (ClientBase screen/field reference, built from the user's UI.Vision
macros and confirmed live against real invoices, including a real ADX booking).

This repo's extraction pipeline is unmodified by that work and stays the source of truth
for JSON schemas/business rules — the ClientBase agent only consumes what this pipeline
already produces. Full rollout plan, current phase status, and open follow-ups (a
cost-optimization pass to cut that agent's per-invoice token spend, and a possible future
move to a self-hosted Browserbase MCP server for persistent login) are tracked in
this project's Claude memory, not here — ask a fresh session to check memory for
"invoice automation 2.0" if picking this up cold.

## Adding New Vendors or Booking Types
1. Add vendor alias to `SYSTEM_PROMPT` in `app/agents/routing_agent.py`
2. Add a new `ruleSet` key and rules constant in the relevant extractor file
3. Update `RULE_SET_MAP` in that extractor
4. If new booking type: create `app/agents/extractors/new_type.py` + add to `EXTRACTOR_MAP` in `extractors/__init__.py`

## Modal Secrets Required
- `anthropic` → contains `ANTHROPIC_API_KEY`
- `resend` → contains `RESEND_API_KEY`, `FROM_EMAIL`, `TO_EMAIL` (outbound results email
  via `app/email_sender.py`, and inbound Receiving API calls via `app/inbound.py`)
- `Invoice_Automation_v2` → contains `RESEND_WEBHOOK_SECRET` (the `whsec_...` signing
  secret Resend issues for the inbound Receiving webhook — verifies `POST /inbound-email`
  requests are actually from Resend; see `app/inbound.py`)
