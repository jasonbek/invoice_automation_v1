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

FINANCIAL PLACEMENT RULE: ALL financial figures — totalBase, totalTax, totalCommission,
deposits, port charges, gratuities, discounts, savings, fare breakdowns — belong on
SCREEN 1 ONLY. Screen 2 (Cruise Details) must contain ZERO currency amounts and ZERO
financial figures. Do not leak pricing into clientItinerary, description, or any other
Screen 2 field.

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
  "clientItinerary": "Chronological day-by-day cruise itinerary as a plain text block. Do NOT include any financial figures (fares, taxes, commission, deposits, port charges, gratuities, discounts) — those belong on Screen 1 ONLY.\\n\\nBegin with this exact header on its own lines:\\n  Itinerary at a glance\\n  ----------------------\\n\\nThen one entry per day from embarkation to debarkation in this exact format (NO times):\\n\\nDay 1 - MM/DD/YYYY - [Port/Location]\\nDay 2 - MM/DD/YYYY - [Port/Location or 'At Sea']\\nDay 3 - MM/DD/YYYY - [Port/Location]\\n...continue through the final day.\\n\\nRules:\\n- Mark the first day as the embarkation port and the last day as the debarkation port.\\n- Sea days: write 'At Sea' as the location.\\n- Use the four-digit year (YYYY), not two-digit.\\n- No arrival/departure times. No prices. No currency amounts.\\n- After the day list, optionally add a short 'Inclusions:' line listing non-financial inclusions (dining package, beverage package, WiFi, shore credits as counts — never dollar amounts)."
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


async def run(markdown: str, routing: dict, exchange_rate_note: str | None = None, today_date: str = "") -> list[dict]:
    """Extract cruise sections from invoice Markdown."""
    rate_line = f"\n{exchange_rate_note}\n" if exchange_rate_note else ""
    date_line = f"TODAY'S DATE: {today_date}\n" if today_date else ""
    user_content = (
        f"VENDOR: {routing.get('vendor', 'Unknown')}\n"
        f"{date_line}\n"
        f"INVOICE MARKDOWN:\n{markdown}\n"
        f"{rate_line}\n"
        "Extract all cruise data and return the JSON array of 2 section objects."
    )

    return await call_claude(_SYSTEM_PROMPT, user_content, max_tokens=1500)
