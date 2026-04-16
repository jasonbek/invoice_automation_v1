"""
Agent 3h: Day Tour Extractor

Handles: Viator on Line, Daytrip, and other day-excursion / shore-excursion
operators. These are NOT multi-day land packages — they go into the Misc
screens in ClientBase Online.

Output:
  - 1 × Screen 1 + 1 × Screen 2 per booking (multiple bookings supported;
    see VIATOR_RULES for the BR-#### splitting rule)
"""

from app.agents.extractors.base import GLOBAL_RULES, call_claude

# ── Vendor-specific rules ──────────────────────────────────────────────────────

VIATOR_RULES = """\
VENDOR RULES — VIATOR ON LINE:

- vendor / serviceProviderName on Screen 1 = "Viator on Line".
- confirmationNumber: ALWAYS the reference that begins with "BR" (e.g. "BR-123456789").
  Look for this at the top of the invoice or in the booking reference section.
- MULTI-BOOKING RULE: Each distinct BR-#### on the invoice is a SEPARATE booking,
  even if the invoice shares a single itinerary number across them. For every BR-####
  found on the invoice, you MUST output its OWN pair of screens: one Screen 1
  (Summary) AND one Screen 2 (Details). If the invoice has 3 BR-#### references,
  output 3 Screen 1 sections + 3 Screen 2 sections (6 sections total), paired in
  order: Screen 1 for BR-A, Screen 2 for BR-A, Screen 1 for BR-B, Screen 2 for BR-B, etc.
  Financials (basePrice, commission, deposit, total, amount owing) must be allocated
  per BR — use the per-activity amounts from the invoice, NOT the invoice grand total.
- Itinerary numbers (if shown, distinct from the BR reference) go into invoiceRemarks — NOT confirmationNumber.
- Default commission is 8% of the base price in CAD unless the invoice explicitly
  states a different percentage or dollar amount.\
"""

DAYTRIP_RULES = """\
VENDOR RULES — DAYTRIP:

- vendor / serviceProviderName on Screen 1 = "Daytrip".
- confirmationNumber: use the Daytrip booking / reference number shown on the invoice
  (often labelled "Booking ID", "Reservation", or similar). DO NOT prefix with "BR-".
- One Screen 1 + one Screen 2 per booking on the invoice. If the invoice contains
  multiple separate Daytrip bookings, pair them in order (Screen 1 + Screen 2 per booking).
- Commission: ALWAYS set commission to "0.00" for Daytrip invoices, even if the
  invoice explicitly states a commission amount or percentage. Daytrip bookings
  are treated as non-commissionable by this agency.
- Do NOT apply the Viator BR-#### rule.\
"""

GENERIC_DAY_TOUR_RULES = """\
VENDOR RULES — GENERIC DAY TOUR OPERATOR:

- vendor / serviceProviderName on Screen 1 = the actual operator name as it appears
  on the invoice. DO NOT substitute "Viator on Line".
- confirmationNumber: the booking / reference number on the invoice. Do NOT invent a
  "BR-" prefix unless the invoice literally uses that format.
- One Screen 1 + one Screen 2 per booking on the invoice.
- Extract commission exactly as shown on the invoice; if not stated, leave as "".\
"""

RULE_SET_MAP = {
    "viator":  VIATOR_RULES,
    "daytrip": DAYTRIP_RULES,
}

SHARED_FINANCIAL_RULES = """\
SHARED FINANCIAL RULES:
- If the invoice is NOT in CAD: convert basePrice to CAD using the provided exchange rate.
  Record commission in the original invoice currency — do NOT convert commission to CAD.
  Then populate agentRemarks with:
    DEPOSIT PAID: $[CAD amount] CAD
    COMMISSION: [raw amount] [currency]
    Invoiced in [currency] by Supplier
    Amounts in CB Converted to CAD on [MM/DD/YY] @ rate of 1 [currency] : [rate] CAD\
"""

# ── System prompt ──────────────────────────────────────────────────────────────

_PROMPT_HEADER = """\
You are a day tour booking data extraction specialist for a travel agency (ClientBase Online).
Extract data from the Markdown invoice and return ONLY a JSON array of section objects.
"""

_PROMPT_SCHEMA = r"""
═══════════════════════════════════════════════
SCHEMA — 1 + 1 sections per booking (multiple bookings supported)
═══════════════════════════════════════════════

### SECTION 1 — Day Tour Screen 1 (Summary) — ONE PER BOOKING
Output one Screen 1 section per booking on the invoice (see vendor rules above for
multi-booking handling — Viator splits on BR-####; others split only if the invoice
shows multiple distinct bookings). Pair it immediately with its matching Screen 2.

{
  "dateReserved": "MM/DD/YY",
  "vendor": "Operator name as it appears on the invoice (e.g. 'Viator on Line', 'Daytrip'). Use the name from the vendor rules above.",
  "confirmationNumber": "Booking reference as defined by the vendor rules above",
  "duration": "Number of days the booking spans (string)",
  "numberOfTravellers": "String",
  "tripType": "International | Transborder | Domestic",
  "basePrice": "Total base price in CAD, 2 decimal places",
  "commission": "Commission amount per vendor rules — do NOT convert to CAD, 2 decimal places",
  "finalPaymentDue": "MM/DD/YY — use \"\" if not stated on invoice",
  "serviceProviderName": "Operator name (matches vendor field)",
  "startDate": "MM/DD/YY — date of the earliest day tour",
  "endDate": "MM/DD/YY — date of the latest day tour",
  "description": "Short overall description of the booking (1–2 sentences)",
  "invoiceRemarks": "Client-facing notes — include individual activity/itinerary numbers here, promotions, inclusions summary, voucher info",
  "agentremarks": "Currency conversion + financial notes (REQUIRED if invoice is not in CAD — use \"\" if CAD)"
}

### SECTION 2 — Day Tour Screen 2 (Details) — ONE PER BOOKING
Paired with its Screen 1 above, in the same order.

{
  "serviceProviderName": "End supplier / operator name — the actual activity provider (this may differ from the booking vendor on Screen 1)",
  "startDate": "MM/DD/YY — tour date",
  "endDate": "MM/DD/YY — same as startDate for a single-day activity",
  "description": "Short description of this specific tour/activity (1–2 sentences)",
  "clientfeedback": "Everything the traveller needs to prepare: start time, meeting point, duration, what to bring, dress code, cancellation policy, voucher redemption instructions, etc. Plain text with line breaks. NO pricing or financial figures.",
  "agentremarks": "Agent notes if applicable — use \"\" if none"
}

═══════════════════════════════════════════════
OUTPUT FORMAT
═══════════════════════════════════════════════
[
  {"sectionTitle": "Day Tour Screen 1 (Summary)", "data": { ... }},
  {"sectionTitle": "Day Tour Screen 2 (Details)", "data": { ... }}
]

IMPORTANT: Use "Day Tour Screen 2 (Details)" as the sectionTitle for EVERY Screen 2 section,
even if there are multiple. Each is a separate JSON object in the array.
Return ONLY the JSON array. No prose, no markdown fences.
"""


def _build_system_prompt(rule_set: str) -> str:
    vendor_rules = RULE_SET_MAP.get(rule_set, GENERIC_DAY_TOUR_RULES)
    return (
        _PROMPT_HEADER
        + "\n" + vendor_rules
        + "\n\n" + SHARED_FINANCIAL_RULES
        + "\n\n" + GLOBAL_RULES
        + _PROMPT_SCHEMA
    )


async def run(markdown: str, routing: dict, exchange_rate_note: str | None = None, today_date: str = "") -> list[dict]:
    """Extract day tour sections from a day-tour invoice Markdown."""
    rule_set = routing.get("ruleSet", "viator")
    vendor   = routing.get("vendor", "Viator on Line")
    system_prompt = _build_system_prompt(rule_set)

    rate_line = f"\n{exchange_rate_note}\n" if exchange_rate_note else ""
    date_line = f"TODAY'S DATE: {today_date}\n" if today_date else ""
    pairing_hint = (
        "Remember: each BR-#### is a separate booking — output one Screen 1 + one Screen 2 per BR-####, paired in order."
        if rule_set == "viator"
        else "Output one Screen 1 + one Screen 2 per booking on the invoice, paired in order."
    )
    user_content = (
        f"VENDOR: {vendor}\n"
        f"RULE SET: {rule_set}\n"
        f"{date_line}\n"
        f"INVOICE MARKDOWN:\n{markdown}\n"
        f"{rate_line}\n"
        f"Extract all day tour data and return the JSON array. {pairing_hint}"
    )

    return await call_claude(system_prompt, user_content, max_tokens=4096)
