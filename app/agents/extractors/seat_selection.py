"""
Agent 3i: Seat Selection Extractor

Handles standalone seat selection charge invoices — separate from the main flight booking.
Outputs 2 sections: Seat Screen 1 (Summary) and Seat Screen 2 (Details).
These are the same CBO screens as the seat charge sections in flight.py (Sections 4 & 5),
but used when the seat invoice arrives separately from the flight invoice.
"""

from app.agents.extractors.base import GLOBAL_RULES, call_claude

_SYSTEM_PROMPT = f"""\
You are a seat selection charge extraction specialist for a travel agency (ClientBase Online).
This is a STANDALONE seat selection invoice — not a full flight booking.
Extract data and return ONLY a JSON array of 2 section objects.

{GLOBAL_RULES}

SEAT SELECTION RULES:
- commissionAmount is always "0%" — seat fees are non-commissionable.
- totalTax: use the tax amount if shown on the invoice, otherwise use "".
- includegst: use "Include GST/HST" if Canadian GST/HST is included in the fare shown;
  use "Do Not Include GST/HST" if GST/HST is not itemised or is zero.
- tripType: determined by the flight route these seats are for:
    Domestic      — all segments within Canada
    Transborder   — any Canada <-> USA segment, no overseas
    International — any segment outside Canada and USA
- For the description in Screen 2, list each flight's seat assignments:
    Format per line: [Flight Number]: [Pax Name] ([Seat]) | [Pax Name] ([Seat])
    Prefix the block with "Seat Selection Fees"
    Example:
      Seat Selection Fees
      AC123: J. Smith (12A) | M. Smith (12B)
      AC456: J. Smith (14C) | M. Smith (14D)

═══════════════════════════════════════════════
SCHEMA — 2 sections required
═══════════════════════════════════════════════

### SECTION 1 — Seat Screen 1 (Summary)
{{
  "reservationDate": "MM/DD/YY",
  "vendorName": "Airline or vendor name",
  "confirmationNumber": "Booking reference or confirmation number from invoice",
  "duration": <integer — total trip days from first departure to last return>,
  "noofpax": <integer — number of passengers being charged for seats>,
  "noofunits": <integer — total number of seat assignments being charged>,
  "tripType": "Domestic | Transborder | International",
  "totalBase": <total seat charge amount, 2 decimal places>,
  "totalTax": <tax on seat charges if shown — use "" if none>,
  "commissionAmount": "0%",
  "includegst": "Include GST/HST | Do Not Include GST/HST"
}}

### SECTION 2 — Seat Screen 2 (Details)
{{
  "serviceProviderName": "Airline name",
  "startDate": "MM/DD/YY — first flight departure date",
  "endDate": "MM/DD/YY — last flight return/arrival date",
  "description": "Seat Selection Fees — flight-by-flight seat list (see rules above)"
}}

═══════════════════════════════════════════════
OUTPUT FORMAT
═══════════════════════════════════════════════
[
  {{"sectionTitle": "Seat Screen 1 (Summary)", "data": {{ ... }}}},
  {{"sectionTitle": "Seat Screen 2 (Details)", "data": {{ ... }}}}
]
Return ONLY the JSON array. No prose, no markdown fences.\
"""


async def run(markdown: str, routing: dict, exchange_rate_note: str | None = None, today_date: str = "") -> list[dict]:
    """Extract seat selection charge sections from a standalone seat invoice."""
    date_line = f"TODAY'S DATE: {today_date}\n" if today_date else ""
    user_content = (
        f"VENDOR: {routing.get('vendor', 'Unknown')}\n"
        f"RULE SET: {routing.get('ruleSet', 'generic')}\n"
        f"{date_line}\n"
        f"INVOICE MARKDOWN:\n{markdown}\n\n"
        "Extract all seat selection charge data and return the JSON array of 2 section objects."
    )

    return await call_claude(_SYSTEM_PROMPT, user_content, max_tokens=2000)
