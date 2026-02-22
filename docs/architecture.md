# Invoice Automation — Architecture & Developer Reference

> **Last updated:** 2026-02-22
> Use this document as the primary reference when making any code changes.

---

## Pipeline Overview

```
[Staff browser]
     │
     │  POST /process-invoice (multipart/form-data)
     ▼
┌─────────────────────────────────────────────────┐
│  Modal.com — FastAPI ASGI (fastapi_entrypoint)  │
│  Returns 202 immediately                        │
│  Spawns run_pipeline() as a background function │
└──────────────────────┬──────────────────────────┘
                       │
           ┌───────────▼────────────┐
           │  Agent 1               │
           │  markdown_agent.py     │
           │  Model: Haiku          │
           │  PDF/eml/md → LABEL:   │
           │  value text extract    │
           └───────────┬────────────┘
                       │  5s sleep (rate limit buffer)
           ┌───────────▼────────────┐
           │  Agent 2               │
           │  routing_agent.py      │
           │  Model: Haiku          │
           │  → vendor, ruleSet,    │
           │    bookingTypes[]      │
           └───────────┬────────────┘
                       │  5s sleep
                       │  + fetch live exchange rate (once)
                       │  + compute today's date (once)
                       │
        ┌──────────────┼──────────────┐
        │   asyncio.gather() ─ parallel│
        ▼              ▼              ▼
   flight.py      tour.py       hotel.py
   cruise.py   insurance.py  service_fee.py
              new_traveller.py
        │   (all Model: Sonnet 4.6)   │
        └──────────────┬──────────────┘
                       │  flat list of sections
           ┌───────────▼────────────┐
           │  email_sender.py       │
           │  Resend API            │
           │  HTML email with:      │
           │  - readable tables     │
           │  - raw JSON blocks     │
           │  - original PDF attach │
           └────────────────────────┘
```

---

## File Map

```
app/
├── main.py                        ← Modal app, FastAPI endpoints, run_pipeline(), form HTML
├── email_sender.py                ← Resend HTML email builder + sender
└── agents/
    ├── markdown_agent.py          ← Agent 1: any file → LABEL:value text
    ├── routing_agent.py           ← Agent 2: text → {vendor, ruleSet, bookingTypes[]}
    └── extractors/
        ├── __init__.py            ← Orchestrator: asyncio.gather(), rate fetch, date inject
        ├── base.py                ← GLOBAL_RULES + call_claude() shared by all extractors
        ├── currency.py            ← Live exchange rate from frankfurter.app
        ├── flight.py              ← Flight: 3 sections
        ├── tour.py                ← Tour/land: 2 sections
        ├── hotel.py               ← Hotel: 2 sections
        ├── cruise.py              ← Cruise: 2 sections
        ├── insurance.py           ← Insurance (Manulife): 2 sections
        ├── service_fee.py         ← Service fee: 2 sections (generated, not LLM-extracted)
        └── new_traveller.py       ← New traveller profile: 3–4 sections
```

---

## Key Data Flows

### What gets injected into every extractor prompt

In `__init__.py`, before `asyncio.gather()`, two values are computed and passed to every extractor:

| Value | Source | Used for |
|---|---|---|
| `exchange_rate_note` | `currency.py` → frankfurter.app API | Non-CAD invoice conversion |
| `today_date` | `date.today().strftime("%m/%d/%y")` | Fallback booking date |

Both are injected as lines in `user_content` when present. Each extractor's `run()` signature:
```python
async def run(markdown, routing, exchange_rate_note=None, today_date="") -> list[dict]
```

### What Agent 1 outputs (markdown_agent.py)

Always `LABEL: value` lines. Key standardization rules:
- **All passenger/traveller/guest names** → always labelled `Passenger:` (one per line), regardless of what the invoice calls them. This feeds `_extract_traveller_name()` in `main.py` for the email subject.
- Values are preserved exactly as printed (dates, amounts, codes).

### What Agent 2 outputs (routing_agent.py)

```json
{
  "vendor": "Air Canada Internet",
  "ruleSet": "air_canada",
  "bookingTypes": ["flight"],
  "serviceFeeIncluded": false
}
```

The `ruleSet` value determines which vendor-specific rules are loaded inside each extractor.

---

## Global Rules (`app/agents/extractors/base.py`)

These apply to **every extractor** via the `GLOBAL_RULES` constant:

| Rule | Detail |
|---|---|
| **Dates** | Always `MM/DD/YY` — convert from any format |
| **Times** | Always 12-hour with AM/PM (e.g., `4:40 PM`) |
| **Missing fields** | DELETE the key — never `null`, `"N/A"`, or `""` |
| **Non-CAD currency** | Use the injected live rate; convert to CAD; populate `agentRemarks` |
| **agentRemarks format** | `DEPOSIT PAID: $X CAD` / `COMMISSION: X [currency]` / `Invoiced in [currency] by Supplier` / `Amounts in CB Converted to CAD on MM/DD/YY @ rate of 1 [currency] : [rate] CAD` |
| **Accented characters** | Strip to ASCII equivalent in ALL string fields (é→e, ô→o, ç→c, etc.) |
| **Booking date fallback** | If no booking date on invoice, use `TODAY'S DATE` from input |
| **Promotions/savings** | Always include in client remarks: `invoiceRemarks` (flights/tours/cruises) or `notesForClient` (hotels) |

---

## Vendor Routing Table (`app/agents/routing_agent.py`)

| Official Name | Triggers on Invoice | ruleSet key |
|---|---|---|
| Air Canada Internet | Air Canada, AC, AirCan | `air_canada` |
| Westjet Internet | West Jet, Westjet, WJ | `westjet` |
| Expedia TAAP | Expedia, TAAP | `expedia` |
| Intair | Travel Brands (flight booking, no COMMISSION line) | `travel_brands` |
| Travel Brands | Travel Brands (tour/land, no COMMISSION line) | `travel_brands` |
| ADX | ADX, or Travel Brands/Intair + explicit COMMISSION line | `adx_intair` |
| Manulife Insurance | Any insurance policy document | `manulife` |
| Viator on Line | Viator | `viator` |
| Tourcan Vacations | TOURCAN VACATIONS, Tourcan | `tourcan` |
| generic | Everything else | `generic` |

**ADX vs. Intair decision logic:**
- Invoice says "Intair" or "Travel Brands" + has explicit `COMMISSION: $X.XX` line → `ADX`, ruleSet `adx_intair`
- Invoice says "Travel Brands" + tour (no COMMISSION line) → `Travel Brands`, ruleSet `travel_brands`
- Invoice says "Travel Brands" + flight (no COMMISSION line) → `Intair`, ruleSet `travel_brands`

---

## Extractor Schemas & Vendor Rules

### Flight (`flight.py`) — 3 sections

**Sections:**

| # | sectionTitle | Data shape |
|---|---|---|
| 1 | Flight Screen 1 (Summary) | Object: reservationDate, vendorName, confirmationNumber, recordLocator, duration, totalBase, totalTax, totalCommission, invoiceRemarks |
| 2 | Flight Screen 2 (Segments) | Array: one object per leg (IATA codes, times, flight number) |
| 3 | Flight Screen 3 (Passengers) | Array: one object per passenger (name, ticket#, base/tax/commission per pax) |

**invoiceRemarks** must always contain a seat map block:
```
Seat Selections
---------------
AC123: J. Smith (12A) | M. Smith (12B)
AC456: Seat: N/A  (check airline site)
```

**recordLocator:** If multiple carriers each have their own locator, join with `/` (e.g., `ABC123/XYZ789`).

**Vendor commission rules (`RULE_SET_MAP` in `flight.py`):**

| ruleSet | Rule |
|---|---|
| `air_canada` | Commission by fare class + route. 0% (Basic/BA/BV/BQ/LGT), 3% (NA Standard/TG or Interline), 4% (NA all other), 5% (Intl Online JV). Mixed fares → lowest rate for NA, international class ignores domestic feeder. |
| `westjet` | Commission by RBD class + route. Mixed fares → higher rate. Call Centre = 0% always. |
| `adx_intair` | Use the exact `COMMISSION: $X.XX` figure verbatim. No calculation. Also: confirmationNumber = TRIP REF, recordLocator = PNR, ticketNumber = TICKET NUMBER. |
| `tourcan` | `TOTAL CREDIT -[number]` line = commission (take absolute value). No calculation. |
| `expedia` / `generic` | Extract as shown on invoice. |

---

### Tour (`tour.py`) — 2 sections

**Sections:**

| # | sectionTitle | Key fields |
|---|---|---|
| 1 | Tour Screen 1 (Summary) | dateReserved, vendor, confirmationNumber, duration, numberOfTravellers, tripType, basePrice (CAD), commission, finalPaymentDue, invoiceRemarks, agentRemarks |
| 2 | Tour Screen 2 (Details) | serviceProviderName, startDate, endDate, category, description, clientFeedback (day-by-day itinerary text block) |

**Vendor rules (`RULE_SET_MAP` in `tour.py`):**

| ruleSet | Rule |
|---|---|
| `travel_brands` | Use tour confirmation code as confirmationNumber. Non-CAD: convert + agentRemarks. |
| `viator` | Default commission = 8% of basePrice unless invoice overrides. Vendor name = "Viator on Line". |
| `generic` | Extract commission as shown. Non-CAD: convert + agentRemarks. |

---

### Hotel (`hotel.py`) — 2 sections

**Sections:**

| # | sectionTitle | Key fields |
|---|---|---|
| 1 | Hotel Screen 1 (Summary) | bookingDate, vendor, confirmationNumber, recordLocator, numberOfNights, numberOfGuests, numberOfUnits, category, baseAmount (CAD), taxAmount, commissionAmount, agentRemarks |
| 2 | Hotel Screen 2 (Details) | serviceProviderName, checkInDate, checkOutDate, checkInTime (default 3:00 PM), checkOutTime (default 11:00 AM), roomCategory, roomDescription, beddingType, notesForClient |

**notesForClient MUST include:**
1. Full hotel address, phone, and email
2. Any "Due at property" amount → `"Due at property: CA $X.XX (city/local tax)"`
3. Any promotional savings → `"Special deal: 15% off — CA $224.91 savings"`

**Vendor rules (`RULE_SET_MAP` in `hotel.py`):**

| ruleSet | Rule |
|---|---|
| `expedia` | baseAmount = Subtotal − Taxes & fees (Subtotal includes taxes). taxAmount = "Taxes & fees" only. Commission = "Total Earnings" label. "Due at property" → notesForClient only. |
| `generic` | Extract as shown. "Due at property" / City tax → notesForClient, not taxAmount. |

---

### Cruise (`cruise.py`) — 2 sections

**Sections:**

| # | sectionTitle | Key fields |
|---|---|---|
| 1 | Cruise Screen 1 (Summary) | reservationDate, vendorName, confirmationNumber, duration, noofpax, noofunit, tripType, totalBase (CAD), totalTax, totalCommission, finalpymntduedate, invoiceRemarks, agentRemarks |
| 2 | Cruise Screen 2 (Details) | shipName, startDate, endDate, category, deck, cabinNumber, diningTime, bedding, description, clientItinerary (full text block) |

No vendor-specific rules currently. Generic only.

---

### Insurance (`insurance.py`) — 2 sections

Always `manulife` ruleSet. Special rules baked into system prompt:
- `vendorName` → always `"Manulife Insurance"`
- `confirmationNumber` → strip letter prefix (e.g., `AGX123456` → `123456`)
- `noofpax` / `noofunits` → always `1` per policy
- `description` → `"[Plan Type] - [Traveller Name]"`

---

### Service Fee (`service_fee.py`) — 2 sections

Generated from form data (not LLM-extracted). Always appended last.
- `reservationDate` / `startDate` / `endDate` → `date.today()`
- `commissionPercentage` → `"100"`
- `clientGstRate` → `"5"`
- `totalBase` → amount from form field
- `noofpax` → one small Claude call to count passengers from markdown

---

### New Traveller Profile (`new_traveller.py`) — 3–4 sections

| # | sectionTitle | Content |
|---|---|---|
| 1 | Profile Screen 1 (Contact) | Household: lastName, firstName (e.g., "John & Jane"), address, postal, phone split |
| 2 | Profile Screen 2 (Traveller 1) | DOB (month/day/year separate), citizenship (ISO 2-letter), email |
| 3 | Profile Screen 3 (Traveller 2) | Same — only if second traveller present |
| 4 | Profile Screen 4 (Preferences) | Plain text block: emergency contact, seating, dietary, loyalty numbers |

---

## How to Make Common Changes

### Add a new vendor to an existing booking type

**Example:** Adding "Globus Tours" with a specific commission rule to tours.

1. **`app/agents/routing_agent.py`** — Add a row to the vendor normalization table:
   ```
   | Globus Tours | Globus, Cosmos | globus |
   ```
   And add to the RULE SET KEY MAPPING section:
   ```
   globus → Globus Tours
   ```

2. **`app/agents/extractors/tour.py`** — Add a rules constant and register it:
   ```python
   GLOBUS_RULES = """\
   VENDOR RULES — GLOBUS TOURS:
   Commission: extract the "Net Rate" line and calculate markup from retail price.
   ...\
   """
   RULE_SET_MAP["globus"] = GLOBUS_RULES
   ```

3. Deploy: `modal deploy app/main.py`

---

### Add a new booking type entirely

**Example:** Adding a "rail" booking type for train tickets.

1. **Create** `app/agents/extractors/rail.py` with:
   - A system prompt defining the schema
   - A `run(markdown, routing, exchange_rate_note=None, today_date="")` function

2. **`app/agents/extractors/__init__.py`** — Import and register:
   ```python
   from app.agents.extractors import rail as rail_ext
   EXTRACTOR_MAP["rail"] = rail_ext.run
   ```

3. **`app/agents/routing_agent.py`** — Add detection signals under BOOKING TYPE DETECTION SIGNALS:
   ```
   - rail: train ticket, seat reservation, rail pass, departure/arrival stations
   ```

4. Deploy: `modal deploy app/main.py`

---

### Add a global rule (applies to all booking types)

Edit the `GLOBAL_RULES` constant in `app/agents/extractors/base.py`.
All extractors import this constant at the top of their system prompt — the change propagates automatically.

---

### Modify a schema field

Each extractor has its schema defined inline in its system prompt template. Edit the field description directly in the relevant extractor file. The schema comments are what Claude reads — keep them precise.

---

## Modal Secrets Required

```bash
# Set once; persists across deploys
modal secret create anthropic ANTHROPIC_API_KEY=sk-ant-...
modal secret create resend RESEND_API_KEY=re_... FROM_EMAIL=invoices@domain.com TO_EMAIL=you@domain.com
```

Verify with: `modal secret list`

---

## Deploy Commands

```bash
# Production deploy (permanent, no terminal needed)
modal deploy app/main.py

# Local testing (terminal must stay open)
modal serve app/main.py

# Syntax check (Python not in PATH on dev machine — use this instead)
# Run from project root in a Python environment
python -m py_compile app/main.py app/agents/markdown_agent.py ...
```

---

## Email Output Format

Each email contains:
- **Subject:** `Invoice: [Traveller Name] — [Vendor] ([Booking Types])`
- **Header:** Traveller name, vendor, booking types, timestamp
- **Body:** One block per CBO section, each with:
  - Readable key/value table (for human review)
  - Dark-background raw JSON block labelled "Raw JSON — copy for UI.Vision" (for macro input)
- **Attachment:** Original invoice file(s) unchanged

**Traveller name extraction order** (`_extract_traveller_name()` in `main.py`):
1. Agent 1 markdown — regex for `Passenger:` label (most reliable, works for all types)
2. Flight Passengers section array → `data[0].passengerName`
3. Profile Contact section → `firstName + lastName`
4. Any dict section with `passengerName` key
5. Fallback: `"Unknown"`

---

## Known Limitations / Watch List for Testing

| Item | Status |
|---|---|
| Vendor not in routing table | Routes as `generic` — usually fine, occasionally misclassifies |
| Multi-invoice PDFs (e.g., combined air+land) | Handled via multiple bookingTypes in parallel |
| Very large PDFs (50+ pages) | May hit Agent 1 token limits — use .md pre-extract if needed |
| Non-Manulife insurance | Would route to `generic` cruise/tour — needs its own extractor if other insurers arise |
| Rate limit errors | Handled by `max_retries=6` (~60s backoff). If still failing, increase sleep between agents |
