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
  "baseAmount": "Number (2 decimal places)",
  "taxAmount": "Number (2 decimal places)",
  "commissionAmount": "Number (2 decimal places)"
}}

### SECTION 2 — Hotel Details
{{
  "serviceProviderName": "Full hotel name",
  "checkInDate": "MM/DD/YY",
  "checkOutDate": "MM/DD/YY",
  "checkInTime": "H:MM AM/PM",
  "checkOutTime": "H:MM AM/PM",
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


async def run(markdown: str, routing: dict) -> list[dict]:
    """Extract hotel sections from invoice Markdown."""
    user_content = (
        f"VENDOR: {routing.get('vendor', 'Unknown')}\n\n"
        f"INVOICE MARKDOWN:\n{markdown}\n\n"
        "Extract all hotel data and return the JSON array of 2 section objects."
    )

    return await call_claude(_SYSTEM_PROMPT, user_content, max_tokens=3000)
