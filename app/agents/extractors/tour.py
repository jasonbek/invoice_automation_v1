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
- If the invoice is NOT in CAD: convert totalBase (basePrice) and commission to CAD
  using the best available exchange rate, then populate agentRemarks with:
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
  "commission": "Amount in CAD, 2 decimal places",
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
  "clientFeedback": "Detailed itinerary as a plain text block with line breaks — include day-by-day breakdown, inclusions, meals, activities as found on invoice. DO NOT include any pricing, commission, deposit amounts, or financial figures — those belong in Screen 1 only."
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

    return await call_claude(system, user_content, max_tokens=4096)
