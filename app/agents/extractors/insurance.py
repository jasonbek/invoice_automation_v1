"""
Agent 3e: Insurance Extractor (Manulife Insurance)

Outputs 2 sections: Summary, Details.
Key rule: strip non-numeric characters from confirmationNumber (remove prefixes like AGX).
"""

from app.agents.extractors.base import GLOBAL_RULES, call_claude

_SYSTEM_PROMPT = f"""\
You are an insurance booking data extraction specialist for a travel agency (ClientBase Online).
Extract data from the Markdown invoice and return ONLY a JSON array of section objects.

{GLOBAL_RULES}

SPECIAL RULES:
- vendorName is ALWAYS "Manulife Insurance" regardless of what the invoice says.
- confirmationNumber: extract NUMBERS ONLY — strip any alphabetic prefix (e.g., "AGX123456" → "123456").
- totalBase: the premium amount BEFORE tax.
- totalCommission: the commission amount as a number.
- noofpax and noofunits are always 1 per policy (one object per traveller policy).
- description format: "[Plan Type] - [Traveller Name]" (e.g., "All-Inclusive Single Trip - John Smith")

═══════════════════════════════════════════════
SCHEMA — 2 sections required
═══════════════════════════════════════════════

### SECTION 1 — Insurance Summary
{{
  "reservationDate": "MM/DD/YY",
  "vendorName": "Manulife Insurance",
  "confirmationNumber": "Numbers only (strip any letter prefix)",
  "duration": <integer — number of days covered>,
  "noofpax": 1,
  "noofunits": 1,
  "tripType": "International | Transborder | Domestic",
  "totalBase": <number — premium before tax, 2 decimal places>,
  "totalCommission": <number — 2 decimal places>
}}

### SECTION 2 — Insurance Details
{{
  "startDate": "MM/DD/YY",
  "endDate": "MM/DD/YY",
  "description": "[Plan Type] - [Traveller Name]"
}}

═══════════════════════════════════════════════
OUTPUT FORMAT
═══════════════════════════════════════════════
[
  {{"sectionTitle": "Insurance Screen 1 (Summary)", "data": {{ ... }}}},
  {{"sectionTitle": "Insurance Screen 2 (Details)", "data": {{ ... }}}}
]
Return ONLY the JSON array. No prose, no markdown fences.\
"""


async def run(markdown: str, routing: dict) -> list[dict]:
    """Extract insurance sections from invoice Markdown."""
    user_content = (
        f"INVOICE MARKDOWN:\n{markdown}\n\n"
        "Extract all insurance policy data and return the JSON array of 2 section objects."
    )

    return await call_claude(_SYSTEM_PROMPT, user_content, max_tokens=2000)
