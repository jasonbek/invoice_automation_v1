"""
Agent 3h: Day Tour Extractor

Handles: Viator on Line (day excursions, shore excursions, single-day activities).
These are NOT multi-day land packages — they go into the Misc screens in ClientBase Online.

Output:
  - 1 × Screen 1 (Summary) — full booking financial + overview detail
  - N × Screen 2 (Details) — one section per individual day tour on the booking
"""

from app.agents.extractors.base import GLOBAL_RULES, call_claude

# ── Vendor-specific rules ──────────────────────────────────────────────────────

VIATOR_RULES = """\
VENDOR RULES — VIATOR ON LINE:

- This is a DAY TOUR booking — not a multi-day land package.
- confirmationNumber: ALWAYS the reference that begins with "BR" (e.g. "BR-123456789").
  Look for this at the top of the invoice or in the booking reference section.
- Individual activity/itinerary numbers (if shown) go into invoiceRemarks — NOT confirmationNumber.
- Default commission is 8% of the base price in CAD unless the invoice explicitly
  states a different percentage or dollar amount.
- A single Viator booking may contain MULTIPLE individual day tours (separate activities).
  You MUST produce one Screen 2 section for EACH individual day tour/activity on the invoice.
- commission field: calculate 8% of the original invoice price (before any conversion) unless
  overridden by invoice. Record commission in the original invoice currency — do NOT convert to CAD.
- If the invoice is NOT in CAD: convert basePrice to CAD using the provided exchange rate,
  then populate agentRemarks with:
    DEPOSIT PAID: $[CAD amount] CAD
    COMMISSION: [raw amount] [currency]
    Invoiced in [currency] by Supplier
    Amounts in CB Converted to CAD on [MM/DD/YY] @ rate of 1 [currency] : [rate] CAD\
"""

# ── System prompt ──────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = f"""\
You are a day tour booking data extraction specialist for a travel agency (ClientBase Online).
Extract data from the Markdown invoice and return ONLY a JSON array of section objects.

{VIATOR_RULES}

{GLOBAL_RULES}

═══════════════════════════════════════════════
SCHEMA — 1 + N sections required
═══════════════════════════════════════════════

### SECTION 1 — Day Tour Screen 1 (Summary)
One section only. Covers the overall booking.

{{
  "dateReserved": "MM/DD/YY",
  "vendor": "Viator on Line",
  "confirmationNumber": "The reference beginning with 'BR' (e.g. 'BR-123456789') — always use this, not the individual activity numbers",
  "duration": "Number of days the booking spans (string)",
  "numberOfTravellers": "String",
  "tripType": "International | Transborder | Domestic",
  "basePrice": "Total base price in CAD, 2 decimal places",
  "commission": "8% of the original invoice price in original currency (or invoice-stated amount) — do NOT convert to CAD, 2 decimal places",
  "finalPaymentDue": "MM/DD/YY — use \"\" if not stated on invoice",
  "serviceProviderName": "Viator on Line",
  "startDate": "MM/DD/YY — date of the earliest day tour",
  "endDate": "MM/DD/YY — date of the latest day tour",
  "description": "Short overall description of the booking (1–2 sentences)",
  "invoiceRemarks": "Client-facing notes — include individual activity/itinerary numbers here (e.g. 'Activity #123456789'), promotions, inclusions summary, voucher info",
  "agentremarks": "Currency conversion + financial notes (REQUIRED if invoice is not in CAD — use \"\" if CAD)"
}}

### SECTION 2 — Day Tour Screen 2 (Details) — ONE PER INDIVIDUAL DAY TOUR
Repeat this section for EACH separate day tour / activity on the invoice.
If there are 3 individual tours on the booking, output 3 Screen 2 sections.

{{
  "serviceProviderName": "End supplier / operator name (not Viator — the actual activity provider)",
  "startDate": "MM/DD/YY — tour date",
  "endDate": "MM/DD/YY — same as startDate for a single-day activity",
  "description": "Short description of this specific tour/activity (1–2 sentences)",
  "clientfeedback": "Everything the traveller needs to prepare: start time, meeting point, duration, what to bring, dress code, cancellation policy, voucher redemption instructions, etc. Plain text with line breaks. NO pricing or financial figures.",
  "agentremarks": "Agent notes if applicable — use \"\" if none"
}}

═══════════════════════════════════════════════
OUTPUT FORMAT
═══════════════════════════════════════════════
[
  {{"sectionTitle": "Day Tour Screen 1 (Summary)", "data": {{ ... }}}},
  {{"sectionTitle": "Day Tour Screen 2 (Details)", "data": {{ ... }}}},
  {{"sectionTitle": "Day Tour Screen 2 (Details)", "data": {{ ... }}}}
]

IMPORTANT: Use "Day Tour Screen 2 (Details)" as the sectionTitle for EVERY Screen 2 section,
even if there are multiple. Each is a separate JSON object in the array.
Return ONLY the JSON array. No prose, no markdown fences.\
"""


async def run(markdown: str, routing: dict, exchange_rate_note: str | None = None, today_date: str = "") -> list[dict]:
    """Extract day tour sections from a Viator invoice Markdown."""
    rate_line = f"\n{exchange_rate_note}\n" if exchange_rate_note else ""
    date_line = f"TODAY'S DATE: {today_date}\n" if today_date else ""
    user_content = (
        f"VENDOR: {routing.get('vendor', 'Viator on Line')}\n"
        f"RULE SET: {routing.get('ruleSet', 'viator')}\n"
        f"{date_line}\n"
        f"INVOICE MARKDOWN:\n{markdown}\n"
        f"{rate_line}\n"
        "Extract all day tour data and return the JSON array. "
        "Remember: one Screen 2 section per individual day tour/activity on this booking."
    )

    return await call_claude(_SYSTEM_PROMPT, user_content, max_tokens=4096)
