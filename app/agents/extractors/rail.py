"""
Agent 3g: Rail Extractor

Handles rail bookings from any operator (VIA Rail, Amtrak, Eurostar, Rail Europe, etc.).
Rail bookings are entered in ClientBase Online as Miscellaneous bookings.
Outputs 2 sections: Summary (Screen 1) and Details (Screen 2) with a segment itinerary.
"""

from app.agents.extractors.base import GLOBAL_RULES, call_claude

# ── Vendor-specific rules ──────────────────────────────────────────────────────

GENERIC_RAIL_RULES = """\
VENDOR RULES — RAIL (GENERIC):

Commission: Rail bookings are typically non-commissionable. Use 0% unless the invoice
  shows an explicit commission figure.

Confirmation number: Use the primary booking/trip reference number. If the invoice contains
  multiple reference numbers for the same booking, join them with '/'
  (e.g. 'REF123/REF456').

Ticket numbers: Each segment may carry its own ticket number. Capture all of them in
  clientFeedback — one line per segment, including every ticket and reference number
  associated with that segment.\
"""

RULE_SET_MAP = {
    "generic": GENERIC_RAIL_RULES,
}

# ── System prompt ──────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a rail booking data extraction specialist for a travel agency (ClientBase Online).
Rail bookings are entered as Miscellaneous bookings in ClientBase.
Extract data from the Markdown invoice and return ONLY a JSON array of 2 section objects.

{vendor_rules}

{global_rules}

═══════════════════════════════════════════════
SCHEMA — 2 sections required
═══════════════════════════════════════════════

### SECTION 1 — Rail Screen 1 (Summary)
{{
  "reservationDate": "MM/DD/YY",
  "vendorName": "Rail operator name (e.g. VIA Rail, Amtrak, Eurostar, Rail Europe)",
  "confirmationNumber": "Primary booking/trip reference — join multiples with '/'",
  "duration": <integer — total trip days from first departure to last arrival>,
  "noofpax": <integer — number of passengers>,
  "noofunits": <integer — number of rail segments>,
  "tripType": "Domestic | Transborder | International",
  "totalBase": <total rail fare, 2 decimal places>,
  "totalTax": <taxes and fees if shown on invoice — omit key if none>,
  "commissionAmount": "0%",
  "gstStatus": "GST Included | GST Not Included"
}}

tripType — determined by the stations on the itinerary:
  Domestic     — all stations within Canada
  Transborder  — any Canada <-> USA station, no overseas
  International — any station outside Canada and USA

### SECTION 2 — Rail Screen 2 (Details)
{{
  "serviceProviderName": "Rail operator name",
  "startDate": "MM/DD/YY — first departure date",
  "endDate": "MM/DD/YY — last arrival date",
  "clientFeedback": "Segment itinerary — one line per segment (see format below)"
}}

clientFeedback format — one line per segment:
  [Origin Station] -> [Destination Station] | [Date] | Train: [Service/Train#] | Ticket: [Ticket#] | Ref: [Ref#]

  - Omit Ticket or Ref fields for a segment if not shown on invoice
  - If multiple passengers each have their own ticket for the same segment,
    list each passenger on a separate line under that segment
  - If the invoice has multiple PDFs (one per segment or per passenger),
    consolidate all segments in order into one clientFeedback block

  Example:
    Vancouver -> Winnipeg | 08/26/24 | Train: VIA 1 (The Canadian) | Ticket: 1234567 | Ref: ABC123
    Winnipeg -> Toronto   | 08/28/24 | Train: VIA 1                 | Ticket: 1234568 | Ref: ABC124

═══════════════════════════════════════════════
OUTPUT FORMAT (return this exact structure)
═══════════════════════════════════════════════
[
  {{"sectionTitle": "Rail Screen 1 (Summary)", "data": {{ ... }}}},
  {{"sectionTitle": "Rail Screen 2 (Details)", "data": {{ ... }}}}
]
Return ONLY the JSON array. No prose, no markdown fences.\
"""


async def run(markdown: str, routing: dict, exchange_rate_note: str | None = None, today_date: str = "") -> list[dict]:
    """Extract rail booking sections from invoice Markdown.

    Args:
        markdown:           Full invoice content from Agent 1.
        routing:            Routing result from Agent 2 (vendor, ruleSet, bookingTypes).
        exchange_rate_note: Live rate string from currency.py, or None if invoice is CAD.
        today_date:         Today's date (MM/DD/YY) as fallback for missing booking dates.

    Returns:
        List of 2 section dicts (Rail Screen 1 Summary, Rail Screen 2 Details).
    """
    rule_set = routing.get("ruleSet", "generic")
    vendor_rules = RULE_SET_MAP.get(rule_set, GENERIC_RAIL_RULES)

    system = _SYSTEM_PROMPT.format(
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
        "Extract all rail booking data and return the JSON array of 2 section objects."
    )

    return await call_claude(system, user_content, max_tokens=4096)
