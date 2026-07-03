"""
Agent 3: Vacation Package Extractor

Handles: Air Canada Vacations, Westjet Vacations — packaged air + hotel bookings
(flight(s) + hotel stay(s) sold together under one package price, optionally with
transfers and/or optional trip insurance bundled in).

NOTE: Do NOT route here for plain Air Canada Internet / Westjet Internet flight
tickets — those keep using flight.py with ruleSet "air_canada" / "westjet".

Unlike a normal tour (tour.py), these packages do NOT get a Tour Screen 2 narrative
itinerary. Instead they get:
  - Tour Screen 1 (Summary)   — financials, same as a normal tour Screen 1
  - Flight Screen 2 (Segments) — one array entry per flight leg, no Flight Screen 1
                                  and no Flight Screen 3 (passengers)
  - Hotel Screen 2 (Details)  — one section per hotel stay, no Hotel Screen 1;
                                  transfers and any opted-in insurance are folded
                                  into notesForClient alongside the usual hotel
                                  contact info

Insurance Screen 1/2 are NEVER created here, even if the client purchased optional
insurance as part of the package — those screens are reserved for standalone
Manulife Insurance invoices (ruleSet "manulife", insurance.py).
"""

from app.agents.extractors.base import GLOBAL_RULES, call_claude

# ── Vendor-specific rules ──────────────────────────────────────────────────────

PACKAGE_RULES = """\
VENDOR RULES — AIR CANADA VACATIONS / WESTJET VACATIONS (packaged air + hotel):

Confirmation number: use the PACKAGE confirmation/booking number (the one that covers the
  whole trip) as confirmationNumber on Tour Screen 1 — NOT an individual flight PNR or a
  hotel-only confirmation number, unless the invoice only shows one number for everything.

Financials: apply ALL package financials (base price, commission, deposit, total, amount
  owing) on Tour Screen 1 ONLY, exactly as the normal tour rules and GLOBAL_RULES describe
  (invoiceRemarks = client-facing financial block, agentRemarks = currency conversion /
  commission notes if not in CAD). Flight Screen 2 and Hotel Screen 2 carry NO financial
  fields — they are itinerary/detail screens only.

Screens required — exactly these, nothing else:
  1. ONE "Tour Screen 1 (Summary)" section.
  2. ONE "Flight Screen 2 (Segments)" section — its data is an array with one object per
     flight leg (outbound, connections, return — all legs in one array, same as a normal
     flight itinerary). Do NOT create a Flight Screen 1 (Summary) or Flight Screen 3
     (Passengers) — those do not apply to vacation packages.
  3. ONE "Hotel Screen 2 (Details)" section per hotel stay in the package (usually one,
     but output one per stay if the package includes more than one hotel, in chronological
     order). Do NOT create a Hotel Screen 1 (Summary) — hotel financials are not broken out
     separately from the package price.
  Do NOT create a Tour Screen 2 (Details) — the day-by-day narrative screen does not apply
  to these packages.

Transfers: if the invoice mentions any airport/resort transfer (included or paid, private
  or shared), describe it in the notesForClient field of the relevant Hotel Screen 2 (see
  below). If the package includes more than one hotel stay, put each stay's transfer info
  on that stay's own Hotel Screen 2.

Optional insurance purchased as part of the package: if the invoice shows the client opted
  into trip/vacation protection insurance as a line item of the package, summarize it
  (plan name, coverage dates/amount if shown) in notesForClient on Hotel Screen 2 — do NOT
  create Insurance Screen 1/2 for this. Insurance screens are ONLY created when the vendor
  on the invoice is literally Manulife Insurance / Manulife (a separate, standalone
  invoice) — never for insurance bundled into an Air Canada Vacations / Westjet Vacations
  package.\
"""

RULE_SET_MAP = {
    "vacation_package": PACKAGE_RULES,
}

# ── System prompt ──────────────────────────────────────────────────────────────

_SYSTEM_PROMPT_TEMPLATE = """\
You are a vacation package booking data extraction specialist for a travel agency
(ClientBase Online). Extract data from the Markdown invoice and return ONLY a JSON
array of section objects.

{vendor_rules}

{global_rules}

═══════════════════════════════════════════════
SCHEMA
═══════════════════════════════════════════════

### Tour Screen 1 (Summary) — exactly one
{{
  "dateReserved": "MM/DD/YY",
  "vendor": "Air Canada Vacations | Westjet Vacations — as classified by the routing agent",
  "confirmationNumber": "String — the PACKAGE confirmation number covering the whole trip",
  "duration": "Number of days (string)",
  "numberOfTravellers": "String",
  "tripType": "International | Transborder | Domestic",
  "basePrice": "Amount in CAD, 2 decimal places (convert if needed)",
  "commission": "Amount in original invoice currency, 2 decimal places — do NOT convert to CAD",
  "finalPaymentDue": "MM/DD/YY",
  "invoiceRemarks": "Client-facing financial block per GLOBAL RULES (deposit/total/amount owing) plus any promotions or savings.",
  "agentRemarks": "Currency conversion + financial notes (REQUIRED if invoice is not in CAD)"
}}

### Flight Screen 2 (Segments) — exactly one section, array with one object per leg
[
  {{
    "serviceprovidercode": "2-letter airline IATA code",
    "serviceprovidername": "Full airline name",
    "flightno": "Flight number digits only (no prefix)",
    "departcitycode": "3-letter IATA airport code",
    "departcityname": "City name",
    "startdate": "MM/DD/YY",
    "starttime": "H:MM AM/PM",
    "arrivecitycode": "3-letter IATA airport code",
    "arrivecityname": "City name",
    "enddate": "MM/DD/YY",
    "endtime": "H:MM AM/PM"
  }}
]

SEGMENT DATE CONTINUITY (overnight / next-day arrivals):
  - The NEXT segment's startdate MUST be >= the PREVIOUS segment's enddate. Never let the
    departure date of a later leg revert to a day earlier than the arrival date of the
    preceding leg. Infer the date from the prior segment's arrival date plus any stated
    layover/overnight if the invoice only shows a time for a connection.

### Hotel Screen 2 (Details) — one section per hotel stay
{{
  "serviceProviderName": "Full hotel name",
  "checkInDate": "MM/DD/YY",
  "checkOutDate": "MM/DD/YY",
  "checkInTime": "H:MM AM/PM — default to '3:00 PM' if not stated on invoice",
  "checkOutTime": "H:MM AM/PM — default to '11:00 AM' if not stated on invoice",
  "roomCategory": "Room category or class code",
  "roomDescription": "Room type description",
  "beddingType": "e.g., King, 2 Queens, Twin",
  "notesForClient": "Hotel contact + property-only info for THIS stay: (1) full hotel address, phone number, and email if shown; (2) any amount due at the property (format: 'Due at property: CA $X.XX (city/local tax)'); (3) transfer details for this stay if the invoice mentions any (e.g. 'Transfers: Private roundtrip airport transfer included'); (4) if optional trip insurance was purchased as part of the package, a one-line summary (e.g. 'Insurance: WestJet Vacations Protection Plan included'). Omit any of (2)-(4) entirely if not applicable — do not write 'N/A' or invent values. DO NOT include deposit, total, amount owing, commission, or package price here — those belong ONLY on Tour Screen 1 invoiceRemarks."
}}

═══════════════════════════════════════════════
OUTPUT FORMAT (return this exact structure)
═══════════════════════════════════════════════
[
  {{"sectionTitle": "Tour Screen 1 (Summary)", "data": {{ ... }}}},
  {{"sectionTitle": "Flight Screen 2 (Segments)", "data": [ ... ]}},
  {{"sectionTitle": "Hotel Screen 2 (Details)", "data": {{ ... }}}}
  // repeat "Hotel Screen 2 (Details)" once per additional hotel stay, in chronological order
]
Return ONLY the JSON array. No prose, no markdown fences.\
"""


async def run(
    markdown: str,
    routing: dict,
    exchange_rate_note: str | None = None,
    today_date: str = "",
) -> list[dict]:
    """Extract vacation package sections (Tour Screen 1 + Flight Screen 2 + Hotel Screen 2)."""
    rule_set = routing.get("ruleSet", "vacation_package")
    vendor_rules = RULE_SET_MAP.get(rule_set, PACKAGE_RULES)

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
        "Extract all vacation package data and return the JSON array of sections "
        "(1 Tour Screen 1 + 1 Flight Screen 2 + one Hotel Screen 2 per hotel stay)."
    )

    return await call_claude(system, user_content, max_tokens=8192)
