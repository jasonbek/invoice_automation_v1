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

# Deploy the app (from project root)
modal deploy app/main.py

# Run locally for testing (serves on localhost)
modal serve app/main.py

# Set the Anthropic API key as a Modal secret
modal secret create anthropic ANTHROPIC_API_KEY=sk-ant-...

# Syntax check all Python files
python -m py_compile app/main.py app/agents/markdown_agent.py app/agents/routing_agent.py app/agents/extractors/base.py app/agents/extractors/flight.py app/agents/extractors/tour.py app/agents/extractors/hotel.py app/agents/extractors/cruise.py app/agents/extractors/insurance.py app/agents/extractors/service_fee.py app/agents/extractors/new_traveller.py
```

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
3. **Missing fields:** DELETE the key — NEVER use `null`, `undefined`, or `"N/A"`
4. **Sections:** Each schema section = separate JSON object = separate Telegram message
5. **Non-CAD invoices (Tour/Cruise):** Must include `agentRemarks` with live conversion rate
6. **Commission:** Never calculate unless rules require; extract exact figure from invoice

## Vendor Routing Keys (`ruleSet`)
| ruleSet | Vendor |
|---|---|
| `air_canada` | Air Canada Internet |
| `westjet` | Westjet Internet |
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

## Adding New Vendors or Booking Types
1. Add vendor alias to `SYSTEM_PROMPT` in `app/agents/routing_agent.py`
2. Add a new `ruleSet` key and rules constant in the relevant extractor file
3. Update `RULE_SET_MAP` in that extractor
4. If new booking type: create `app/agents/extractors/new_type.py` + add to `EXTRACTOR_MAP` in `extractors/__init__.py`

## Modal Secrets Required
- `anthropic` → contains `ANTHROPIC_API_KEY`
