"""
Agent 3d: Cruise Extractor

Outputs 2 sections: Summary, Details.
Cruise invoices often include itinerary text blocks — preserve these in clientItinerary.
Non-CAD invoices must have agentRemarks with conversion details.
"""

from app.agents.extractors.base import GLOBAL_RULES, call_claude

_SYSTEM_PROMPT = f"""\
You are a cruise booking data extraction specialist for a travel agency (ClientBase Online).
Extract data from the Markdown invoice and return ONLY a JSON array of section objects.

{GLOBAL_RULES}

CURRENCY NOTE: If the invoice is NOT in CAD, convert totalBase to CAD using the best
available exchange rate and populate agentRemarks:
  DEPOSIT PAID: $[CAD amount] CAD
  COMMISSION: [raw amount] [currency]
  Invoiced in [currency] by Supplier
  Amounts in CB Converted to CAD on [MM/DD/YY] @ rate of 1 [currency] : [rate] CAD

═══════════════════════════════════════════════
SCHEMA — 2 sections required
═══════════════════════════════════════════════

### SECTION 1 — Cruise Summary
{{
  "reservationDate": "MM/DD/YY",
  "vendorName": "Cruise line or booking vendor name",
  "confirmationNumber": "String",
  "duration": "Number of nights (string)",
  "noofpax": "String (number of passengers)",
  "noofunit": "String (number of cabins)",
  "tripType": "International | Transborder | Domestic",
  "totalBase": "Amount in CAD, 2 decimal places",
  "totalTax": "String",
  "totalCommission": "String (in supplier currency)",
  "finalpymntduedate": "MM/DD/YY",
  "invoiceRemarks": "Client-facing notes, discounts, included items",
  "agentRemarks": "Currency conversion and financial notes"
}}

### SECTION 2 — Cruise Details
{{
  "shipName": "String",
  "startDate": "MM/DD/YY (embarkation date)",
  "endDate": "MM/DD/YY (debarkation date)",
  "category": "Cabin category code",
  "deck": "String",
  "cabinNumber": "String",
  "diningTime": "String",
  "bedding": "String",
  "description": "Stateroom description",
  "clientItinerary": "Plain text block — full itinerary at a glance, port days, inclusions, formatted with line breaks"
}}

═══════════════════════════════════════════════
OUTPUT FORMAT
═══════════════════════════════════════════════
[
  {{"sectionTitle": "Cruise Screen 1 (Summary)", "data": {{ ... }}}},
  {{"sectionTitle": "Cruise Screen 2 (Details)", "data": {{ ... }}}}
]
Return ONLY the JSON array. No prose, no markdown fences.\
"""


async def run(markdown: str, routing: dict, exchange_rate_note: str | None = None) -> list[dict]:
    """Extract cruise sections from invoice Markdown."""
    rate_line = f"\n{exchange_rate_note}\n" if exchange_rate_note else ""
    user_content = (
        f"VENDOR: {routing.get('vendor', 'Unknown')}\n\n"
        f"INVOICE MARKDOWN:\n{markdown}\n"
        f"{rate_line}\n"
        "Extract all cruise data and return the JSON array of 2 section objects."
    )

    return await call_claude(_SYSTEM_PROMPT, user_content, max_tokens=4096)
