# Invoice Automation v1 — Detailed Specification

## 1. Purpose

A travel agency automation pipeline that eliminates manual data entry into **ClientBase Online** (CBO) — the agency's CRM. Staff upload supplier invoices via a web form, and the system reads the PDF, classifies it, and returns pre-structured JSON matching every CBO data entry screen, delivered as Telegram messages.

---

## 2. System Architecture

```
[Staff]  →  n8n Form  →  POST /process-invoice  →  Modal.com (async)
                                                        │
                                           ┌────────────▼────────────┐
                                           │  Agent 1: Markdown      │
                                           │  (PDF → LABEL:value)    │
                                           └────────────┬────────────┘
                                                        │
                                           ┌────────────▼────────────┐
                                           │  Agent 2: Router        │
                                           │  (→ vendor + types)     │
                                           └────────────┬────────────┘
                                                        │
                                    ┌───────────────────┼────────────────────┐
                                    │ (parallel)        │                    │
                             ┌──────▼──────┐    ┌───────▼──────┐   ┌────────▼──────┐
                             │ flight.py   │    │  tour.py     │   │  hotel.py etc │
                             └──────┬──────┘    └───────┬──────┘   └────────┬──────┘
                                    └───────────────────┼────────────────────┘
                                                        │
                                           ┌────────────▼────────────┐
                                           │  POST callback_url      │
                                           │  → n8n Webhook          │
                                           └────────────┬────────────┘
                                                        │
                                             Loop each section
                                                        │
                                           ┌────────────▼────────────┐
                                           │  Telegram Message       │
                                           │  (one per CBO screen)   │
                                           └─────────────────────────┘
```

---

## 3. Infrastructure

| Component | Technology | Notes |
|---|---|---|
| Hosting | Modal.com (serverless) | Free tier; auto-scales to zero |
| HTTP framework | FastAPI inside Modal ASGI | Two endpoints (multipart + JSON) |
| LLM | Anthropic Claude API | Haiku for Agents 1 & 2; Sonnet 4.6 for extractors |
| Secrets | Modal Secrets (`anthropic`) | `ANTHROPIC_API_KEY` |
| Workflow automation | n8n Cloud (v2.4.8) | Form input + Telegram output |
| Python version | 3.11 | Runs in Modal Debian Slim image |

---

## 4. API Endpoints

### `GET /health`
Returns `{"status": "ok"}`. Used for uptime checks.

### `POST /process-invoice` (multipart/form-data)
Primary endpoint for n8n. Returns `202 Accepted` immediately; processing is async.

| Field | Type | Required | Description |
|---|---|---|---|
| `vendor` | string | yes | Supplier name hint from form |
| `callback_url` | string | yes | n8n webhook URL to receive results |
| `service_fee` | float | no (default 0) | Agency service fee amount |
| `booking_type_hint` | string | no | Hint: `flight`, `tour`, `hotel`, etc. |
| `files` | UploadFile[] | yes | PDF, .eml, or .md invoice files |

### `POST /process-invoice-json` (application/json)
Alternate endpoint for n8n when multipart binary handling is problematic. Files passed as base64 strings.

---

## 5. Agent Pipeline (4 Stages)

### Stage 1 — Markdown Agent (`app/agents/markdown_agent.py`)

**Model:** `claude-haiku-4-5-20251001`
**Input:** List of base64-encoded files
**Output:** Compact `LABEL: value` text extract

**File handling:**
- **PDF** → sent to Claude natively as a `document` block
- **.eml (email)** → parsed with Python's `email` module; body text and embedded PDFs extracted separately (avoids sending 50K+ tokens of MIME noise)
- **Plain text / .md** → decoded UTF-8, sent as text

**Output format:** Only `LABEL: value` lines — no prose, no headers, no markdown. Extracts: confirmation numbers, PNRs, ticket numbers, names, fare codes, flight segments, dates, times, amounts, tour/hotel/cruise/insurance fields, personal data.

---

### Stage 2 — Routing Agent (`app/agents/routing_agent.py`)

**Model:** `claude-haiku-4-5-20251001`
**Input:** Markdown from Stage 1 + vendor hint + booking type hint
**Output:** JSON classification object

```json
{
  "vendor": "Air Canada Internet",
  "ruleSet": "air_canada",
  "bookingTypes": ["flight"],
  "serviceFeeIncluded": true
}
```

**Vendor normalization table:**

| Official Name | Aliases / Triggers |
|---|---|
| Air Canada Internet | Air Canada, AC, AirCan |
| Westjet Internet | West Jet, Westjet, WJ |
| Expedia TAAP | Expedia, TAAP |
| Intair | Travel Brands — flight booking only |
| Travel Brands | Travel Brands — tour/land booking |
| ADX | ADX, or Intair + explicit COMMISSION line |
| Manulife Insurance | Any insurance policy document |
| Viator | Viator |

**Key ADX vs. Intair logic:** If invoice header says "Intair"/"Travel Brands" AND has an explicit `COMMISSION: $X.XX` line → vendor = ADX, ruleSet = `adx_intair`.

**Booking type detection:** Signals in the markdown determine which types are present. A single invoice can yield multiple types (e.g., `["flight", "tour"]`).

---

### Stage 3 — Extractor Agents (`app/agents/extractors/`)

**Model:** `claude-sonnet-4-6` (all extractors)
**Orchestration:** `asyncio.gather()` — all required extractors run in parallel
**Input:** Markdown + routing dict
**Output:** List of section dicts `[{"sectionTitle": "...", "data": {...}}]`

#### 3a. Flight Extractor (`flight.py`)
Produces **3 sections**:

| Section | Title | Data Shape |
|---|---|---|
| 1 | Flight Screen 1 (Summary) | Object: reservationDate, vendorName, confirmationNumber, recordLocator, duration, totalBase, totalTax, totalCommission, invoiceRemarks (seat map) |
| 2 | Flight Screen 2 (Segments) | Array: one object per flight leg (IATA codes, times, dates) |
| 3 | Flight Screen 3 (Passengers) | Array: one object per passenger (name, ticket#, base/tax/commission per pax) |

**Vendor-specific commission rules:**
- **Air Canada:** 0% (Economy Basic), 3% (North America Standard/TG), 4% (NA all other), 5% (International Online JV), 3% (International Interline). Excludes taxes, fees, SMB tickets.
- **WestJet:** By RBD class and route region. Mixed fares → higher rate.
- **ADX/Intair:** Use the exact `COMMISSION: $X.XX` dollar figure verbatim. No percentage calculation.
- **Expedia / Generic:** Extract as shown.

**Seat mapping rule:** `invoiceRemarks` must contain a formatted seat block listing every flight segment and assigned seats (or "Seat: N/A" if unknown).

#### 3b. Tour Extractor (`tour.py`)
Produces **2 sections**:

| Section | Title | Key Fields |
|---|---|---|
| 1 | Tour Screen 1 (Summary) | dateReserved, vendor, confirmationNumber, duration, numberOfTravellers, tripType, basePrice (CAD), commission, finalPaymentDue, invoiceRemarks, agentRemarks |
| 2 | Tour Screen 2 (Details) | serviceProviderName, startDate, endDate, category, description, clientFeedback (day-by-day itinerary) |

**Currency rule:** If invoice is NOT in CAD, convert to CAD and populate `agentRemarks` with: deposit paid, raw commission, currency, conversion date, exchange rate.

**Viator special rule:** Default commission = 8% of base price unless overridden.

#### 3c. Hotel Extractor (`hotel.py`)
Produces **2 sections**:

| Section | Title | Key Fields |
|---|---|---|
| 1 | Hotel Screen 1 (Summary) | bookingDate, vendor, confirmationNumber, recordLocator, numberOfNights, numberOfGuests, numberOfUnits, category, baseAmount, taxAmount, commissionAmount |
| 2 | Hotel Screen 2 (Details) | serviceProviderName, checkIn/OutDate, checkIn/OutTime, roomCategory, roomDescription, beddingType, notesForClient (MUST include address, phone, email) |

#### 3d. Cruise Extractor (`cruise.py`)
Produces **2 sections**:

| Section | Title | Key Fields |
|---|---|---|
| 1 | Cruise Screen 1 (Summary) | reservationDate, vendorName, confirmationNumber, duration, noofpax, noofunit, tripType, totalBase (CAD), totalTax, totalCommission, finalpymntduedate, invoiceRemarks, agentRemarks |
| 2 | Cruise Screen 2 (Details) | shipName, startDate, endDate, category, deck, cabinNumber, diningTime, bedding, description, clientItinerary (full text block) |

**Currency rule:** Same as tour — convert to CAD and populate `agentRemarks` if not CAD.

#### 3e. Insurance Extractor (`insurance.py`)
Produces **2 sections**: Summary + Details.
**Special rules:**
- `vendorName` always hardcoded to `"Manulife Insurance"`
- `confirmationNumber`: strip alphabetic prefix (e.g., `AGX123456` → `123456`)
- `noofpax` / `noofunits` always 1 per policy
- `description` format: `"[Plan Type] - [Traveller Name]"`

#### 3f. Service Fee (`service_fee.py`)
Produces **2 sections**: Summary + Details.
**Special:** Data is generated from form input, not the invoice. Uses a minimal Claude call only to determine passenger count. Always appended last.

| Field | Value |
|---|---|
| vendorName | `"Service Fee"` |
| chargedAs | `"Per Booking"` |
| commissionPercentage | `"100"` |
| clientGstRate | `"5"` |
| totalBase | Amount from form |
| description | `"Agency Planning Fee"` |

#### 3g. New Traveller Profile (`new_traveller.py`)
Produces **3–4 sections**:

| Section | Title | Description |
|---|---|---|
| 1 | Profile Screen 1 (Contact) | Household: name, address, postal code, phone |
| 2 | Profile Screen 2 (Traveller 1) | DOB, citizenship, email |
| 3 | Profile Screen 3 (Traveller 2) | Same — only included if second traveller present |
| 4 | Profile Screen 4 (Preferences) | Plain text block: emergency contact, seating, dietary, loyalty numbers |

**Formatting rules:** Province/state as 2-letter code, citizenship as ISO 2-letter code, phone split into area code (3 digits) + number (7 digits), birthMonth as full name.

---

### Stage 4 — Callback Delivery (`app/main.py`)

After all extractors complete, `run_pipeline` POSTs results to `callback_url`:

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

On any exception:
```json
{ "status": "error", "error": "...", "sections": [] }
```

---

## 6. Global Formatting Rules (enforced on all extractors)

| Rule | Spec |
|---|---|
| Dates | `MM/DD/YY` — always, no exceptions |
| Times | 12-hour with AM/PM (e.g., `4:40 PM`) |
| Missing fields | **Delete the key** — never `null`, `"N/A"`, or `""` |
| Currency | Extract exact figure; only convert if schema requires CAD |
| Commission | Never calculate unless vendor rules require it |
| Output | JSON array only — no prose, no markdown code fences |

---

## 7. Rate Limiting Strategy

- Agents 1 & 2 (Haiku): fast, low cost, separate rate limit pool
- 5-second sleep inserted between Haiku agents and Sonnet extractors to avoid bursting quotas
- All Claude clients use `max_retries=6` (~60s total exponential backoff) to handle Tier 1 rate limits

---

## 8. Supported Booking Types & Vendor Matrix

| Booking Type | ruleSet keys supported |
|---|---|
| `flight` | `air_canada`, `westjet`, `adx_intair`, `expedia`, `generic` |
| `tour` | `travel_brands`, `viator`, `generic` |
| `hotel` | `generic` |
| `cruise` | `generic` |
| `insurance` | `manulife` |
| `new_traveller` | (no vendor rules — profile-only) |
| `service_fee` | (generated; always appended if fee > 0) |

---

## 9. Extensibility

**Add a new vendor:**
1. Add alias to routing agent's vendor normalization table
2. Add rules constant + `RULE_SET_MAP` entry in relevant extractor
3. Redeploy: `modal deploy app/main.py`

**Add a new booking type:**
1. Create `app/agents/extractors/new_type.py` with a `run(markdown, routing)` function
2. Register it in `EXTRACTOR_MAP` in `app/agents/extractors/__init__.py`
3. Add detection signals to routing agent's system prompt

---

## 10. Known State / In Progress

- n8n input workflow: **working** (form → multipart POST → 202 response confirmed)
- n8n output workflow: **in progress** — webhook node must be in production-activated state (not test mode) to receive callbacks
- Python not in PATH on dev machine — syntax checking requires `modal serve` for live validation

---

*Generated: 2026-02-19*
