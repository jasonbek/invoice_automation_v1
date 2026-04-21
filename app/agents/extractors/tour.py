"""
Agent 3b: Tour Extractor

Handles: Travel Brands, Intair (tour), generic tour operators.
NOTE: Viator is handled by day_tour.py — do not route viator ruleSet here.
Outputs 2 sections: Summary, Details.

Input contract:
  - markdown:      compact LABEL: value extract from Agent 1 — used as a cross-
                   reference for confirmation numbers, pricing, passenger names.
  - source_blocks: (optional) the raw Anthropic content blocks (PDFs + mammoth
                   Markdown + email body) that were fed to Agent 1. When present,
                   Sonnet re-reads the untouched document to produce the day-by-
                   day "Itinerary at a glance" — the narrative is otherwise lost
                   in Agent 1's LABEL:value filtering. Falls back gracefully to
                   markdown-only when source_blocks are not supplied.

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
  "clientFeedback": {client_feedback_rules}
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

# ── clientFeedback rules (Itinerary at a glance) ───────────────────────────────
# Stored as a JSON-safe string literal and injected into the schema template.

_CLIENT_FEEDBACK_RULES = (
    "\"Chronological day-by-day itinerary as a plain text block, built ONLY from "
    "explicit information in the supplier document. Do NOT hallucinate transfers, "
    "tours, accommodation, or summaries.\\n\\n"
    "Begin with this exact header on its own lines:\\n"
    "  Itinerary at a glance\\n"
    "  ----------------------\\n\\n"
    "Then one entry per day in this exact pipe-delimited format (four columns):\\n\\n"
    "Day N | MM/DD/YYYY | Location | Headline\\n\\n"
    "═══ COLUMN DEFINITIONS ═══\\n"
    "- Day N: use the supplier document's OWN 'Day N' numbering if printed in a per-day heading. "
    "If the document does NOT use 'Day N' numbering, compute Day 1 = startDate, Day 2 = startDate + 1 day, "
    "and so on through endDate. Every calendar day from startDate to endDate (inclusive) gets a row "
    "UNLESS the OWN ARRANGEMENTS rule below excludes it.\\n"
    "- MM/DD/YYYY: the calendar date for that day — use FOUR-digit year (e.g. '10/17/2026'), "
    "NOT the MM/DD/YY format used elsewhere.\\n"
    "- Location: city / region / route for that day. On travel days use ' / ' to show the route "
    "(e.g. 'Naples / Pompeii / Herculaneum', 'Rome / Florence').\\n"
    "- Headline: a single line combining up to three event categories on that day, joined with '; ' "
    "(semicolon + space). Include the categories in this order: Transportation, Accommodation, Tour(s). "
    "Include only categories that have explicit events on that day — DO NOT invent events.\\n\\n"
    "═══ HEADLINE CATEGORIES ═══\\n\\n"
    "1. TRANSPORTATION — any scheduled movement on that day. This REPLACES the old 'Flights' category "
    "and covers ALL modes. Include whichever of these the document explicitly states for that day:\\n"
    "   - Flight: 'Flight [XX####] [ORIG] → [DEST] (dep [time], [cabin class])'\\n"
    "   - Rail / train: 'Rail [train#] [From] → [To] (dep [time])'\\n"
    "   - Private transfer / chauffeur / luxury minivan: 'Private transfer [From] → [To] (pickup [time])'\\n"
    "   - Coach / shared transfer / shuttle: 'Coach transfer [From] → [To] ([time])'\\n"
    "   - Ferry / boat / hydrofoil: 'Ferry [From] → [To] ([time])'\\n"
    "   - Car rental: 'Car rental pickup [location]' / 'Car rental drop-off [location]'\\n"
    "   - When the mode is unstated but the document shows arrival/departure: 'Arrive [city]' / 'Depart [city]'\\n"
    "   Multiple transport events on the same day: join with ' + '.\\n\\n"
    "2. ACCOMMODATION — hotel check-in and check-out events only. Do NOT emit a line on continuing-stay "
    "nights (only emit on the night the guest actually checks in, and on the morning of check-out).\\n"
    "   - Check-in:  'Check-in [Hotel Name]'\\n"
    "   - Check-out: 'Check-out [Hotel Name]'\\n"
    "   - Relocation day (check-out of one hotel + check-in of another): join with ' / '\\n"
    "   Derive from the hotel table or In:/Out: dates mapped to the matching calendar date.\\n\\n"
    "3. TOUR(S) — named guided tours, excursions, shore excursions, activities, cooking classes, "
    "tastings, etc. Use the supplier's own name for the tour.\\n"
    "   - Format: '[Tour Name] ([duration]; [start time if stated])'\\n"
    "   - Examples:\\n"
    "       'Private Walking Tour: National Archaeological Museum (4hr; 2:30 PM)'\\n"
    "       'Pompeii & Herculaneum Private Guided Tours (7hr; pickup 9:00 AM)'\\n"
    "   - Multiple tours on the same day: join with ' + '.\\n\\n"
    "═══ HEADLINE OVERRIDE — PER-DAY HEADING TITLE ═══\\n"
    "If the supplier's document has an explicit per-day heading with a verbatim TITLE "
    "(e.g. 'Day 3 – Tuesday 14 July 2026: PENANG HERITAGE WALK'), use that TITLE verbatim "
    "as the Tour(s) portion of the Headline — preserve the supplier's wording and capitalization. "
    "Transportation and Accommodation events on that same day are still appended to the headline as "
    "separate '; '-joined items.\\n\\n"
    "═══ AT LEISURE RULE ═══\\n"
    "If the supplier's program for that day explicitly says 'at leisure', 'free at leisure', "
    "'day at leisure', 'days at leisure', or similar, AND no other Transportation / Accommodation / "
    "Tour events exist for that day, the Headline is exactly 'At leisure'. A date range such as "
    "'Tuesday October 20 – Wednesday October 21, 2026: Days at leisure' marks EACH calendar day in "
    "that range as 'At leisure'. NEVER use 'At leisure' as a fallback for days the document does "
    "not cover.\\n\\n"
    "═══ OWN ARRANGEMENTS RULE — strict ═══\\n"
    "- If a heading or date range says 'OWN ARRANGEMENTS', 'OWN ARRAGEMENTS' (accept this misspelling), "
    "'Own arrangements', or 'Own arrangement', SKIP those days ENTIRELY. Do NOT emit a row. "
    "Leave a gap in the day numbering.\\n"
    "- Range form: 'Day 5 – Thursday 16 July 2026 – Day 9 – Monday 20 July 2026: OWN ARRAGEMENTS' "
    "means skip Day 5, 6, 7, 8, 9.\\n"
    "- Hotel-table form: if the accommodation column says 'Own arrangements' for a date range, "
    "skip every calendar day inside that range (map In:/Out: dates to calendar days).\\n"
    "- NEVER substitute 'At leisure' for a skipped Own-Arrangements day.\\n\\n"
    "═══ OTHER RULES ═══\\n"
    "- Every date, hotel name, flight number, train number, time, transfer, tour name and city "
    "MUST come from the supplier document. Never invent values.\\n"
    "- NEVER fabricate days that are not present in the supplier document.\\n"
    "- Times in the headline use 12-hour format with AM/PM (e.g. '2:30 PM', '9:00 AM').\\n"
    "- No financial figures anywhere in this block (no prices, commission, deposits).\\n\\n"
    "At the very end of the block, append this exact disclaimer on its own line (blank line before it):\\n\\n"
    "Please refer to the supplier documentation for further detail; it takes precedence over this "
    "outline in the event of any discrepancies.\""
)


async def run(
    markdown: str,
    routing: dict,
    exchange_rate_note: str | None = None,
    today_date: str = "",
    source_blocks: list[dict] | None = None,
) -> list[dict]:
    """Extract tour sections from invoice Markdown.

    When source_blocks are provided, Sonnet re-reads the raw supplier document
    directly — necessary for the day-by-day "Itinerary at a glance" block, which
    relies on per-day headings that Agent 1's LABEL:value filter strips out.
    """
    rule_set = routing.get("ruleSet", "generic")
    vendor_rules = RULE_SET_MAP.get(rule_set, GENERIC_TOUR_RULES)

    system = _SYSTEM_PROMPT_TEMPLATE.format(
        vendor_rules=vendor_rules,
        global_rules=GLOBAL_RULES,
        client_feedback_rules=_CLIENT_FEEDBACK_RULES,
    )

    rate_line = f"\n{exchange_rate_note}\n" if exchange_rate_note else ""
    date_line = f"TODAY'S DATE: {today_date}\n" if today_date else ""

    instruction_text = (
        f"VENDOR: {routing.get('vendor', 'Unknown')}\n"
        f"RULE SET: {rule_set}\n"
        f"{date_line}\n"
        f"COMPACT EXTRACT (from Agent 1 — cross-reference for confirmation numbers, "
        f"pricing, passenger names):\n{markdown}\n"
        f"{rate_line}\n"
        "The attached supplier document above is the authoritative source for the "
        "day-by-day itinerary (clientFeedback). Use it for the 'Itinerary at a glance' "
        "block. Use the compact extract above for confirming fields on Screen 1. "
        "Return the JSON array of 2 section objects."
    )

    if source_blocks:
        user_content: str | list[dict] = list(source_blocks) + [
            {"type": "text", "text": instruction_text}
        ]
    else:
        user_content = instruction_text

    return await call_claude(system, user_content, max_tokens=8192)
