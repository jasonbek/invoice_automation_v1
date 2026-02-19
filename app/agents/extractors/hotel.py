"""
Agent 3c: Hotel Extractor

Outputs 2 sections: Summary, Details.
The Details section MUST include the full hotel address, phone, and email in notesForClient.
"""

from app.agents.extractors.base import GLOBAL_RULES, call_claude

_SYSTEM_PROMPT = f"""\
You are a hotel booking data extraction specialist for a travel agency (ClientBase Online).
Extract data from the Markdown invoice and return ONLY a JSON array of section objects.

{GLOBAL_RULES}

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
  "notesForClient": "MUST include: full hotel address, phone number, email, and any special notes for the client"
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


async def run(markdown: str, routing: dict, exchange_rate_note: str | None = None) -> list[dict]:
    """Extract hotel sections from invoice Markdown."""
    rate_line = f"\n{exchange_rate_note}\n" if exchange_rate_note else ""
    user_content = (
        f"VENDOR: {routing.get('vendor', 'Unknown')}\n\n"
        f"INVOICE MARKDOWN:\n{markdown}\n"
        f"{rate_line}\n"
        "Extract all hotel data and return the JSON array of 2 section objects."
    )

    return await call_claude(_SYSTEM_PROMPT, user_content, max_tokens=3000)
