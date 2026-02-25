"""
Agent 3a: Flight Extractor

Handles: Air Canada Internet, Westjet Internet, ADX/Intair, Expedia TAAP, Tourcan Vacations, generic airlines.
Outputs 3 sections: Summary, Segments (array), Passenger Details (array).
"""

from app.agents.extractors.base import GLOBAL_RULES, call_claude

# ── Vendor-specific commission rules ──────────────────────────────────────────

AIR_CANADA_RULES = """\
VENDOR RULES — AIR CANADA INTERNET:

Inclusions: Commission applies to base fare, surcharges, and stopover charges.
Exclusions: Do NOT apply commission to taxes, change fees, seat selection fees, meals,
  upgrades, infants not occupying a seat, SMB tickets (PN#), corporate contract tickets.

Mandatory Tour Code: ACTOT is required in the tour code box for ALL destinations EXCEPT
  North America and Sun destinations. If ACTOT is missing or incorrect, add this to
  invoiceRemarks: "ACTOT REQUIRED — VERIFY: ticketing error fee applies (min $50)"

Service Combination Rule: If the itinerary mixes "Service Canada" with any of (Sun,
  South America, Transatlantic, Transpacific), apply the LOWER rate to the entire ticket.

Mixed Fare Class — North America: Mixed classes on same ticket → apply LOWEST rate.
Mixed Fare Class — International: Apply rate based on the LOWEST booking class of the
  international segments. Domestic "feeder" legs do NOT downgrade international commission.

JV Carriers (Transatlantic): Air Canada, Lufthansa, Austrian, Swiss, Brussels Airlines,
  Edelweiss, Discover, United.
JV Carriers (Mainland China): Air Canada and Air China.

COMMISSION RATES (check fare basis code on each ticket):
  0% — Economy Basic: fare basis ending in BA, BV, BQ, or LGT (all regions)
  3% — North America & Sun: Economy Standard (fare basis ending in TG)
  3% — International Interline: non-JV partner segments (South America / Transatlantic /
       Mainland China / Transpacific)
  4% — North America & Sun: all other Economy, Premium Economy, Business
  5% — International Online: AC or JV-operated (South America / Transatlantic /
       Mainland China / Transpacific)\
"""

WESTJET_RULES = """\
VENDOR RULES — WESTJET INTERNET:

Call Centre bookings: ALWAYS 0% commission — do not calculate anything.
Mixed Fare Rule: If a ticket has multiple fare classes, apply the HIGHER commission amount.

COMMISSION RATES by RBD class and route:
  Class E:              0%  Domestic/Transborder  |  0%  Latin America & Caribbean  |  0%  Transatlantic/ROW
  Classes L,K,T,X,S,N,Q,H: 3%  Dom/Trans          |  7%  LAC                        |  7%  Transatlantic/ROW
  Classes M,B,Y:        5%  Dom/Trans              |  8%  LAC                        |  9%  Transatlantic/ROW
  Classes R,O,W:        8%  Dom/Trans              |  8%  LAC                        | 10%  Transatlantic/ROW
  Classes D,C,J:       10%  Dom/Trans              |  8%  LAC                        | 15%  Transatlantic/ROW\
"""

ADX_INTAIR_RULES = """\
VENDOR RULES — ADX / INTAIR:

Commission: If the invoice has an explicit line labelled "COMMISSION" with a dollar amount
  (e.g., "CAD $75.00"), use THAT EXACT figure for totalCommission and commission.
  Do NOT calculate percentages — use the number verbatim.

Locator fields (map from invoice labels):
  confirmationNumber = value next to "TRIP REF" label on invoice
  recordLocator      = value next to "PNR" label on invoice
  ticketNumber       = value next to "TICKET NUMBER" label on invoice\
"""

TOURCAN_RULES = """\
VENDOR RULES — TOURCAN VACATIONS:

Commission: The invoice has a line labelled "TOTAL CREDIT" with a negative dollar amount
  (e.g., "TOTAL CREDIT -75.00"). Use the absolute value of that figure as totalCommission
  and commission for each passenger. Do NOT calculate percentages.
  Example: "TOTAL CREDIT -75.00" → totalCommission = "75.00", commission = "75.00"\
"""

GENERIC_FLIGHT_RULES = """\
VENDOR RULES — GENERIC:
Extract commission percentage or amount exactly as shown on the invoice.
Use the standard PNR code as recordLocator.\
"""

RULE_SET_MAP = {
    "air_canada": AIR_CANADA_RULES,
    "westjet": WESTJET_RULES,
    "adx_intair": ADX_INTAIR_RULES,
    "expedia": GENERIC_FLIGHT_RULES,
    "tourcan": TOURCAN_RULES,
}

# Vendors that use per-passenger ticketing in ClientBase Screen 3 —
# totalBase/totalTax/totalCommission are NOT needed on Screen 1 for these.
_TICKETING_VENDORS = {"air_canada", "westjet", "adx_intair"}

# ── Section 1 schema variants ──────────────────────────────────────────────────

# Used for air_canada, westjet, adx_intair — totals live in Screen 3 per passenger.
_SECTION1_SUMMARY_ONLY = """\
### SECTION 1 — Flight Summary
{{
  "reservationDate": "MM/DD/YY",
  "vendorName": "Normalized vendor name (e.g., Air Canada Internet)",
  "confirmationNumber": "String",
  "recordLocator": "String — if multiple locators exist (e.g. different carriers), join them with '/' (e.g. 'ABC123/XYZ789')",
  "duration": <integer — total trip days>,
  "invoiceRemarks": "Seat selections block (see rules below)"
}}\
"""

# Used for tourcan, expedia, generic — no per-passenger ticketing section in ClientBase.
_SECTION1_FULL = """\
### SECTION 1 — Flight Summary
{{
  "reservationDate": "MM/DD/YY",
  "vendorName": "Normalized vendor name (e.g., Air Canada Internet)",
  "confirmationNumber": "String",
  "recordLocator": "String — if multiple locators exist (e.g. different carriers), join them with '/' (e.g. 'ABC123/XYZ789')",
  "duration": <integer — total trip days>,
  "totalBase": <number with 2 decimal places>,
  "totalTax": <number — sum of carrier surcharges and fees>,
  "totalCommission": "String — percentage (e.g., '4%') OR exact dollar if ADX/Intair",
  "invoiceRemarks": "Seat selections block (see rules below)"
}}\
"""

# ── System prompt ──────────────────────────────────────────────────────────────

_SYSTEM_PROMPT_TEMPLATE = """\
You are a flight booking data extraction specialist for a travel agency (ClientBase Online).
Extract data from the Markdown invoice and return ONLY a JSON array of section objects.

{vendor_rules}

{global_rules}

═══════════════════════════════════════════════
SCHEMA — 3 sections required
═══════════════════════════════════════════════

{section1_schema}

SEAT MAPPING RULES for invoiceRemarks:
  - Scan the ENTIRE document for seat assignments
  - Format per line: [Flight Number]: [Pax Name] ([Seat]) | [Pax Name] ([Seat])
  - One line per flight segment
  - If seats unknown for a segment: [Flight Number]: Seat: N/A  (check airline site)
  Example:
    Seat Selections
    ---------------
    AC123: J. Smith (12A) | M. Smith (12B)
    AC456: Seat: N/A

### SECTION 2 — Flight Segments (array — one object per flight leg)
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

### SECTION 3 — Passenger Details (one object per passenger)
{{
  "passengerName": "Full Name",
  "ticketNumber": "String — omit the first 3 digits (airline code prefix); e.g. '0141234567890' → '1234567890'",
  "basePricePerPassenger": <base fare for this passenger from invoice, 2 decimal places>,
  "taxPerPassenger": <taxes and carrier fees for this passenger from invoice, 2 decimal places>,
  "commission": "Commission for this passenger — percentage (e.g. '4%') OR exact dollar amount"
}}

═══════════════════════════════════════════════
SEAT CHARGES (CONDITIONAL — Sections 4 & 5)
═══════════════════════════════════════════════
Include ONLY if the invoice contains explicit seat selection charges with a dollar amount.
If no seat charges appear, output exactly 3 sections and stop.

If seat charges ARE present, append these 2 sections after Section 3:

#### SECTION 4 — Seat Screen 1 (Summary)
{{
  "reservationDate": "MM/DD/YY — same as flight",
  "vendorName": "Same vendor as flight",
  "confirmationNumber": "Same as flight",
  "duration": <integer — same trip duration>,
  "noofpax": <integer — number of passengers charged for seats>,
  "noofunits": <integer — total seat assignments being charged>,
  "tripType": "Domestic | Transborder | International",
  "totalBase": <total seat charge amount, 2 decimal places>,
  "totalTax": <tax on seat charges if shown on invoice — use "" if none>,
  "commissionAmount": "0%",
  "includegst": "Include GST/HST | Do Not Include GST/HST"
}}

tripType — determined by the main flight route:
  Domestic     — all segments within Canada
  Transborder  — any Canada <-> USA segment, no overseas
  International — any segment outside Canada and USA

#### SECTION 5 — Seat Screen 2 (Details)
{{
  "serviceProviderName": "Airline name",
  "startDate": "MM/DD/YY — first flight departure date",
  "endDate": "MM/DD/YY — last flight return/arrival date",
  "description": "Seat Selection Fees — [copy the seat-by-flight list from invoiceRemarks]"
}}

═══════════════════════════════════════════════
OUTPUT FORMAT (return this exact structure)
═══════════════════════════════════════════════
[
  {{"sectionTitle": "Flight Screen 1 (Summary)", "data": {{ ... }}}},
  {{"sectionTitle": "Flight Screen 2 (Segments)", "data": [ ... ]}},
  {{"sectionTitle": "Flight Screen 3 (Passengers)", "data": [ ... ]}},
  // Only if seat charges exist on invoice:
  {{"sectionTitle": "Seat Screen 1 (Summary)", "data": {{ ... }}}},
  {{"sectionTitle": "Seat Screen 2 (Details)", "data": {{ ... }}}}
]
Return ONLY the JSON array. No prose, no markdown fences.\
"""


async def run(markdown: str, routing: dict, exchange_rate_note: str | None = None, today_date: str = "") -> list[dict]:
    """Extract flight sections from invoice Markdown.

    Args:
        markdown:           Full invoice content from Agent 1.
        routing:            Routing result from Agent 2 (vendor, ruleSet, bookingTypes).
        exchange_rate_note: Live rate string from currency.py, or None if invoice is CAD.

    Returns:
        List of 3 section dicts (Summary, Segments, Passengers).
    """
    rule_set = routing.get("ruleSet", "generic")
    vendor_rules = RULE_SET_MAP.get(rule_set, GENERIC_FLIGHT_RULES)
    section1_schema = (
        _SECTION1_SUMMARY_ONLY if rule_set in _TICKETING_VENDORS else _SECTION1_FULL
    )

    system = _SYSTEM_PROMPT_TEMPLATE.format(
        vendor_rules=vendor_rules,
        global_rules=GLOBAL_RULES,
        section1_schema=section1_schema,
    )

    rate_line = f"\n{exchange_rate_note}\n" if exchange_rate_note else ""
    date_line = f"TODAY'S DATE: {today_date}\n" if today_date else ""
    user_content = (
        f"VENDOR: {routing.get('vendor', 'Unknown')}\n"
        f"RULE SET: {rule_set}\n"
        f"{date_line}\n"
        f"INVOICE MARKDOWN:\n{markdown}\n"
        f"{rate_line}\n"
        "Extract all flight data and return the JSON array of sections (3 sections, or 5 if seat charges are present)."
    )

    return await call_claude(system, user_content, max_tokens=8192)
