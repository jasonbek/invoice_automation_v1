"""
Agent 3c: Hotel Extractor

Outputs 2 sections: Summary, Details.
The Details section MUST include the full hotel address, phone, and email in notesForClient.
"""

from app.agents.extractors.base import GLOBAL_RULES, call_claude

# ── Vendor-specific rules ──────────────────────────────────────────────────────

MULTI_HOTEL_SPLIT_RULE = """\
MULTI-HOTEL SPLIT RULE (Expedia TAAP and BedsonLine):
- If the invoice contains MULTIPLE distinct hotel stays under a single booking
  number / itinerary / confirmation, treat each hotel stay as a SEPARATE booking.
- For EVERY hotel stay on the invoice, output its OWN pair of screens: one
  Screen 1 (Summary) + one Screen 2 (Details). Pair them in order:
  Hotel A → Screen 1 + Screen 2, Hotel B → Screen 1 + Screen 2, etc.
- Financials (baseAmount, taxAmount, commissionAmount, deposit, total, amount
  owing) must be allocated PER hotel — use the per-hotel amounts from the
  invoice, NOT the invoice grand total.
- The shared invoice-level confirmation number is reused on each Screen 1.
  If a hotel-specific confirmation number also exists, use that on its own
  Screen 1 instead.\
"""

EXPEDIA_RULES = """\
VENDOR RULES — EXPEDIA TAAP (Hotel):

Base amount calculation:
  The "Room price" or "Subtotal" line on Expedia invoices INCLUDES taxes and fees.
  Do NOT use it directly as baseAmount.
  baseAmount = Subtotal (Room price) − Taxes & fees
  Example: Room price CA $1,402.02 − Taxes & fees CA $127.44 = baseAmount $1,274.58

Tax amount:
  taxAmount = the "Taxes & fees" line only.
  Do NOT include "Due at property" or "City/local tax" — those are paid by the client
  directly at the hotel and must not appear in taxAmount.

Commission:
  Look for a line labelled "Total Earnings" — this is the commission amount for
  Expedia TAAP invoices. Use that figure verbatim as commissionAmount.

Due at property:
  Any "Due at property" or "City/local tax" line is paid by the client directly at
  the hotel. Do NOT include it in taxAmount. Note it in notesForClient instead:
  "Due at property: CA $X.XX (city/local tax)"\
"""

BEDSONLINE_RULES = """\
VENDOR RULES — BEDSONLINE (Hotel):
- vendor on Screen 1 = "BedsonLine".
- Extract baseAmount, taxAmount, and commission exactly as shown on the invoice.
- Apply the MULTI-HOTEL SPLIT RULE below when the invoice has more than one hotel.\
"""

GENERIC_HOTEL_RULES = """\
VENDOR RULES — GENERIC HOTEL:
Extract base amount, tax amount, and commission exactly as shown on the invoice.
If any amount is labelled "Due at property", "City tax", or "Local tax", note it in
notesForClient — do NOT add it to taxAmount.\
"""

RULE_SET_MAP = {
    "expedia":    EXPEDIA_RULES,
    "bedsonline": BEDSONLINE_RULES,
}

# ── System prompt ──────────────────────────────────────────────────────────────

_SYSTEM_PROMPT_TEMPLATE = """\
You are a hotel booking data extraction specialist for a travel agency (ClientBase Online).
Extract data from the Markdown invoice and return ONLY a JSON array of section objects.

{vendor_rules}

{multi_hotel_rule}

{global_rules}

═══════════════════════════════════════════════
SCHEMA — 2 sections required
═══════════════════════════════════════════════

### SECTION 1 — Hotel Summary
{{
  "bookingDate": "MM/DD/YY",
  "vendor": "Normalized vendor name",
  "confirmationNumber": "String",
  "recordLocator": "String",
  "numberOfNights": "String",
  "numberOfGuests": "String",
  "numberOfUnits": "String (number of rooms)",
  "category": "International | Transborder | Domestic",
  "baseAmount": "Number (2 decimal places, in CAD — convert if needed)",
  "taxAmount": "Number (2 decimal places)",
  "commissionAmount": "Number (2 decimal places)",
  "invoiceRemarks": "Client-facing financial summary block per global rules (Payments are in X / amount shown below is in CAD / Deposit / Total / Amount owing) plus any promotions or savings.",
  "agentRemarks": "Currency conversion details (REQUIRED if invoice is not in CAD — see global rules)"
}}

### SECTION 2 — Hotel Details
{{
  "serviceProviderName": "Full hotel name",
  "checkInDate": "MM/DD/YY",
  "checkOutDate": "MM/DD/YY",
  "checkInTime": "H:MM AM/PM — default to '3:00 PM' if not stated on invoice",
  "checkOutTime": "H:MM AM/PM — default to '11:00 AM' if not stated on invoice",
  "roomCategory": "Room category or class code",
  "roomDescription": "Room type description",
  "beddingType": "e.g., King, 2 Queens, Twin",
  "notesForClient": "Hotel contact + property-only info ONLY. MUST include: (1) full hotel address, phone number, and email; (2) any amount due at the property (e.g. city/local tax not included in the booking total — format as 'Due at property: CA $X.XX (city/local tax)'). DO NOT include deposit, total, amount owing, commission, savings/promotions, or any other financial summary — those belong in Screen 1 invoiceRemarks."
}}

═══════════════════════════════════════════════
OUTPUT FORMAT
═══════════════════════════════════════════════
[
  {{"sectionTitle": "Hotel Screen 1 (Summary)", "data": {{ ... }}}},
  {{"sectionTitle": "Hotel Screen 2 (Details)", "data": {{ ... }}}}
]
Return ONLY the JSON array. No prose, no markdown fences.\
"""


async def run(markdown: str, routing: dict, exchange_rate_note: str | None = None, today_date: str = "") -> list[dict]:
    """Extract hotel sections from invoice Markdown."""
    rule_set = routing.get("ruleSet", "generic")
    vendor_rules = RULE_SET_MAP.get(rule_set, GENERIC_HOTEL_RULES)

    system = _SYSTEM_PROMPT_TEMPLATE.format(
        vendor_rules=vendor_rules,
        multi_hotel_rule=MULTI_HOTEL_SPLIT_RULE,
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
        "Extract all hotel data and return the JSON array of 2 section objects."
    )

    return await call_claude(system, user_content, max_tokens=3000)
