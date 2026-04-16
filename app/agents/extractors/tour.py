"""
Agent 3b: Tour Extractor

Handles: Travel Brands, Intair (tour), generic tour operators.
NOTE: Viator is handled by day_tour.py — do not route viator ruleSet here.
Outputs 2 sections: Summary, Details.

Note on currency: If the invoice is NOT in CAD, Claude must include conversion details
in agentRemarks (the model uses its best available rate knowledge and flags for verification).
"""

from app.agents.extractors.base import GLOBAL_RULES, call_claude

# ── Vendor-specific rules ──────────────────────────────────────────────────────

TRAVEL_BRANDS_RULES = """\
VENDOR RULES — TRAVEL BRANDS / INTAIR (Tour):

- Use the tour confirmation code as confirmationNumber.
- If the invoice is NOT in CAD: convert totalBase (basePrice) to CAD using the exchange rate.
  Do NOT convert commission — record it in the original invoice currency.
  Then populate agentRemarks with:
    DEPOSIT PAID: $[CAD amount] CAD
    COMMISSION: [raw amount] [currency]
    Invoiced in [currency] by Supplier
    Amounts in CB Converted to CAD on [MM/DD/YY] @ rate of 1 [currency] : [rate] CAD
- finalPaymentDue: use the "Final Payment Due" or "Balance Due" date on the invoice.\
"""

GENERIC_TOUR_RULES = """\
VENDOR RULES — GENERIC TOUR OPERATOR:

- Extract commission exactly as shown on the invoice.
- If invoice is not in CAD, convert to CAD and populate agentRemarks with conversion details.\
"""

RULE_SET_MAP = {
    "travel_brands": TRAVEL_BRANDS_RULES,
}

# ── System prompt ──────────────────────────────────────────────────────────────

_SYSTEM_PROMPT_TEMPLATE = """\
You are a tour booking data extraction specialist for a travel agency (ClientBase Online).
Extract data from the Markdown invoice and return ONLY a JSON array of section objects.

{vendor_rules}

{global_rules}

═══════════════════════════════════════════════
SCHEMA — 2 sections required
═══════════════════════════════════════════════

### SECTION 1 — Tour Summary
{{
  "dateReserved": "MM/DD/YY",
  "vendor": "Normalized vendor name",
  "confirmationNumber": "String",
  "duration": "Number of days (string)",
  "numberOfTravellers": "String",
  "tripType": "International | Transborder | Domestic",
  "basePrice": "Amount in CAD, 2 decimal places (convert if needed)",
  "commission": "Amount in original invoice currency, 2 decimal places — do NOT convert to CAD",
  "finalPaymentDue": "MM/DD/YY",
  "invoiceRemarks": "Client-facing notes (discounts, inclusions summary)",
  "agentRemarks": "Currency conversion + financial notes (REQUIRED if invoice is not in CAD)"
}}

### SECTION 2 — Tour Details
{{
  "serviceProviderName": "Tour operator or ground operator name",
  "startDate": "MM/DD/YY",
  "endDate": "MM/DD/YY",
  "category": "Category, class, or tier code if shown",
  "description": "High-level tour description (1–2 sentences)",
  "clientFeedback": "Chronological day-by-day itinerary as a plain text block, built ONLY from explicit information in the supplier document. Do NOT hallucinate activities, sightseeing, or summaries.\\n\\nBegin with this exact header on its own lines:\\n  Itinerary at a glance\\n  ----------------------\\n\\nThen one entry per day in this exact pipe-delimited format (four columns):\\n\\nDay [N] | MM/DD/YYYY | [Location] | [Headline]\\n\\nDAY NUMBERING — use the supplier document's OWN 'Day N' numbering exactly as printed in the per-day headings. Do NOT renumber 1..N from the trip start date. If the document skips numbers (e.g. Day 1, 2, 3, then jumps to Day 10), the output MUST reflect that gap — leave the missing days out.\\n\\nCOLUMN DEFINITIONS:\\n- Day [N]: the day number as printed in the document heading.\\n- MM/DD/YYYY: the calendar date for that day (four-digit year).\\n- Location: the city/region for that day (e.g. 'Penang', 'Penang / Kuala Lumpur' on transit days showing departure / arrival).\\n- Headline: see the priority rule below.\\n\\nHEADLINE PRIORITY (use the highest-priority source available; then append lower-priority items on the same day joined with '; '):\\n  1. PER-DAY HEADING TITLE — scan for lines shaped like 'Day N – <Weekday> <DD Month YYYY>: <TITLE>' (often bold/h1/h2/h3). Strip the 'Day N – <Weekday> <DD Month YYYY>:' prefix and use <TITLE> VERBATIM (preserve supplier's wording and capitalization). This is the PRIMARY headline whenever present.\\n  2. FLIGHTS on that day: 'Flight [XX####] - [Origin] to [Destination] ([dep time] - [arr time], [cabin class])'.\\n  3. HOTEL EVENTS on that day: 'Check-in [Hotel Name]' / 'Check-out [Hotel Name]'. Derive from the HOTEL summary table's In:/Out: dates mapped to the matching Day N by calendar date.\\n  4. ONLY if NONE of the above exist AND the supplier's program for that day explicitly says 'free at leisure' / 'at leisure' / similar → write 'At leisure'. Never use 'At leisure' as a fallback for unknown days.\\n\\nOWN ARRANGEMENTS RULE — strict:\\n- If a heading or date range says 'OWN ARRANGEMENTS', 'OWN ARRAGEMENTS' (accept this misspelling), 'Own arrangements', or 'Own arrangement', SKIP those days ENTIRELY. Do NOT emit a row for them. Leave a gap in the day numbering.\\n- Accept the range form: 'Day 5 – Thursday 16 July 2026 – Day 9 – Monday 20 July 2026: OWN ARRAGEMENTS' means skip Day 5, 6, 7, 8, 9.\\n- Accept the HOTEL-table form: if the accommodation column says 'Own arrangements' for a date range, skip every calendar day inside that range (map In:/Out: dates to Day N).\\n- NEVER substitute 'At leisure' for a skipped Own-Arrangements day. The traveller may have independent bookings; labelling it 'At leisure' misrepresents the invoice. A skipped day produces NO row.\\n\\nOTHER RULES:\\n- Every date, hotel name, flight number, time, and city MUST come from the supplier document. Never invent values.\\n- NEVER fabricate days that are not present in the supplier document.\\n- No financial figures anywhere in this block (no prices, commission, deposits).\\n\\nAt the very end of the block, append this exact disclaimer on its own line (blank line before it):\\n\\nPlease refer to the supplier documentation for further detail; it takes precedence over this outline in the event of any discrepancies."
}}

═══════════════════════════════════════════════
OUTPUT FORMAT
═══════════════════════════════════════════════
[
  {{"sectionTitle": "Tour Screen 1 (Summary)", "data": {{ ... }}}},
  {{"sectionTitle": "Tour Screen 2 (Details)", "data": {{ ... }}}}
]
Return ONLY the JSON array. No prose, no markdown fences.\
"""


async def run(markdown: str, routing: dict, exchange_rate_note: str | None = None, today_date: str = "") -> list[dict]:
    """Extract tour sections from invoice Markdown."""
    rule_set = routing.get("ruleSet", "generic")
    vendor_rules = RULE_SET_MAP.get(rule_set, GENERIC_TOUR_RULES)

    system = _SYSTEM_PROMPT_TEMPLATE.format(
        vendor_rules=vendor_rules,
        global_rules=GLOBAL_RULES,
    )

    rate_line = f"\n{exchange_rate_note}\n" if exchange_rate_note else ""
    date_line = f"TODAY'S DATE: {today_date}\n" if today_date else ""
    user_content = (
        f"VENDOR: {routing.get('vendor', 'Unknown')}\n"
        f"RULE SET: {rule_set}\n"
        f"{date_line}\n"
        f"INVOICE MARKDOWN:\n{markdown}\n"
        f"{rate_line}\n"
        "Extract all tour data and return the JSON array of 2 section objects."
    )

    return await call_claude(system, user_content, max_tokens=8192)
