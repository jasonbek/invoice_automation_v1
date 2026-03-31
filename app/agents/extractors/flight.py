"""
Agent 3a: Flight Extractor

Handles: Air Canada Internet, Westjet Internet, ADX/Intair, Expedia TAAP, Tourcan Vacations, generic airlines.
Outputs 3 sections: Summary, Segments (array), Passenger Details (array).

Commission rates for Air Canada and WestJet are loaded at runtime from
docs/Commissions/ (mounted at /commission_docs/ in the Modal container).
No code change is required when rates are updated — just drop a new .md file
in docs/Commissions/ and redeploy.
"""

from app.agents.extractors.base import GLOBAL_RULES, call_claude
from app.agents.commissions.loader import load_all as _load_commission_docs

# ── Vendor-specific rules ──────────────────────────────────────────────────────
# These contain business logic (what commission applies to, exclusions, etc.).
# Rate tables are NOT here — they come from the loaded commission documents.

AIR_CANADA_RULES = """\
VENDOR RULES — AIR CANADA INTERNET:

Inclusions: Commission applies to base fare, surcharges, and stopover charges.
Exclusions: Do NOT apply commission to taxes, change fees, seat selection fees, meals,
  upgrades, infants not occupying a seat, SMB tickets (PN#), corporate contract tickets,
  Aeroplan redemption tickets, industry reduced rates, ACV air-only tickets, net/IT/BT fares.

Mandatory Tour Code: ACTOT is required in the tour code box for ALL destinations EXCEPT
  North America and Sun destinations. If ACTOT is missing or incorrect, add this to
  invoiceRemarks: "ACTOT REQUIRED — VERIFY: ticketing error fee applies (min $50)"

Service Combination Rule: If the itinerary mixes "Service Canada" with any other service,
  the LOWER rate between Service Canada and the other service applies to the entire ticket.

Mixed Fare Class — North America/Sun: Mixed booking classes on same ticket → apply LOWEST rate.
Mixed Fare Class — International: Apply rate based on the LOWEST booking class of the
  international segments only. Domestic "feeder" legs do NOT downgrade international commission.

JV Carriers — Transatlantic: Air Canada, Lufthansa, Austrian, Swiss, Brussels Airlines,
  Edelweiss, Discover, United.
JV Carriers — Mainland China: Air Canada and Air China.

Online vs Interline:
  Online  = all segments marketed/operated by Air Canada Carriers (or JV carriers for Transatlantic/China).
  Interline = some segments on non-JV partners (max 2 segments per direction).

COMMISSION RATE LOOKUP: Use the official commission tables in the COMMISSION DOCUMENTS section
  below to determine the correct rate. Match the booking class and route type to the correct
  appendix. Check whether any active promotions override the base rate for this ticket's
  plating carrier, booking class, and travel/ticketing dates.\
"""

WESTJET_RULES = """\
VENDOR RULES — WESTJET INTERNET:

Call Centre bookings: ALWAYS 0% commission — do not calculate anything.
Mixed Fare Rule: If a ticket has multiple fare classes, apply the HIGHER commission amount.

Networks:
  Domestic        — all segments within Canada
  Transborder     — Canada <-> USA (no overseas)
  LAC             — Canada <-> Mexico, Caribbean, Central America
  Transatlantic/RoW — any segment to/from Europe, Middle East, Africa, Asia-Pacific, etc.

COMMISSION RATE LOOKUP: Use the official commission tables in the COMMISSION DOCUMENTS section
  below to match the RBD booking class and network to the correct rate.\
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

Commission: The invoice has a line with a negative dollar amount at the end
  (e.g., "TOTAL CREDIT -75.00"). This negative amount IS the agency commission — it is
  NOT a discount or promotional saving. Use its absolute value as totalCommission.
  Do NOT calculate percentages. Do NOT include it in invoiceRemarks as a saving or discount.
  Example: "TOTAL CREDIT -75.00" → totalCommission = "75.00"\
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

# Rule sets that use real commission documents (all others extract from invoice directly)
_COMMISSION_DOC_VENDORS = {"air_canada", "westjet"}

# Vendors that use per-passenger ticketing in ClientBase Screen 3 —
# totalBase/totalTax/totalCommission are NOT needed on Screen 1 for these.
_TICKETING_VENDORS = {"air_canada", "westjet", "adx_intair"}

# Vendors that do NOT use a passenger details screen (Screen 3) at all.
_NO_PASSENGER_SCREEN_VENDORS = {"tourcan"}

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
  "invoiceRemarks": "Seat selections block (see rules below).",
  "agentremarks": "Commission rationale — one line stating the rate chosen and why (appendix/table, booking class, route, and any active promo that overrides the base rate)."
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
  "invoiceRemarks": "Seat selections block (see rules below)",
  "agentremarks": ""
}}\
"""

# ── System prompt ──────────────────────────────────────────────────────────────

_SECTION3_PASSENGERS = """\
### SECTION 3 — Passenger Details (one object per passenger)
{{
  "passengerName": "Full Name",
  "ticketNumber": "String — omit the first 3 digits (airline code prefix); e.g. '0141234567890' → '1234567890'",
  "basePricePerPassenger": <base fare for this passenger from invoice, 2 decimal places>,
  "taxPerPassenger": <taxes and carrier fees for this passenger from invoice, 2 decimal places>,
  "commission": "Commission for this passenger — percentage (e.g. '4%') OR exact dollar amount"
}}\
"""

_OUTPUT_FORMAT_WITH_PASSENGERS = """\
[
  {{"sectionTitle": "Flight Screen 1 (Summary)", "data": {{ ... }}}},
  {{"sectionTitle": "Flight Screen 2 (Segments)", "data": [ ... ]}},
  {{"sectionTitle": "Flight Screen 3 (Passengers)", "data": [ ... ]}},
  // Only if seat charges exist on invoice:
  {{"sectionTitle": "Seat Screen 1 (Summary)", "data": {{ ... }}}},
  {{"sectionTitle": "Seat Screen 2 (Details)", "data": {{ ... }}}}
]\
"""

_OUTPUT_FORMAT_NO_PASSENGERS = """\
[
  {{"sectionTitle": "Flight Screen 1 (Summary)", "data": {{ ... }}}},
  {{"sectionTitle": "Flight Screen 2 (Segments)", "data": [ ... ]}},
  // Only if seat charges exist on invoice:
  {{"sectionTitle": "Seat Screen 1 (Summary)", "data": {{ ... }}}},
  {{"sectionTitle": "Seat Screen 2 (Details)", "data": {{ ... }}}}
]\
"""

_SYSTEM_PROMPT_TEMPLATE = """\
You are a flight booking data extraction specialist for a travel agency (ClientBase Online).
Extract data from the Markdown invoice and return ONLY a JSON array of section objects.

{vendor_rules}

{global_rules}
{commission_section}
═══════════════════════════════════════════════
SCHEMA — {section_count} sections required
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

{section3_schema}
═══════════════════════════════════════════════
SEAT CHARGES (CONDITIONAL — appended after last section)
═══════════════════════════════════════════════
Include ONLY if the invoice contains explicit seat selection charges with a dollar amount.
If no seat charges appear, output exactly {section_count} sections and stop.

If seat charges ARE present, append these 2 sections:

#### SEAT Screen 1 (Summary)
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

#### SEAT Screen 2 (Details)
{{
  "serviceProviderName": "Airline name",
  "startDate": "MM/DD/YY — first flight departure date",
  "endDate": "MM/DD/YY — last flight return/arrival date",
  "description": "Seat Selection Fees — [copy the seat-by-flight list from invoiceRemarks]"
}}

═══════════════════════════════════════════════
OUTPUT FORMAT (return this exact structure)
═══════════════════════════════════════════════
{output_format}
Return ONLY the JSON array. No prose, no markdown fences.\
"""

_COMMISSION_SECTION_TEMPLATE = """\

═══════════════════════════════════════════════
COMMISSION DOCUMENTS (official airline rate tables)
═══════════════════════════════════════════════
Use these documents to determine the correct commission rate.
Cross-reference the booking class, route type, plating carrier, and — for promotions —
the ticketing date and travel date shown on the invoice.

{commission_docs}

RATIONALE REQUIREMENT: In agentremarks (Screen 1), include one line that states:
  - The rate chosen and why (table/appendix used, booking class, route type)
  - Whether a promotion overrides the base rate, and if so which one
  Example (base rate):   "Commission: 4% — AC North America, Economy Y (Appendix 5)"
  Example (promo):       "Commission: 8% — LH Group promo Mar2026, Business J, plating LH220 (overrides 5% base)"
  Example (call centre): "Commission: 0% — WestJet Call Centre booking (non-commissionable)"\
"""


async def run(markdown: str, routing: dict, exchange_rate_note: str | None = None, today_date: str = "") -> list[dict]:
    """Extract flight sections from invoice Markdown.

    Args:
        markdown:           Full invoice content from Agent 1.
        routing:            Routing result from Agent 2 (vendor, ruleSet, bookingTypes).
        exchange_rate_note: Live rate string from currency.py, or None if invoice is CAD.
        today_date:         Today's date (MM/DD/YY) as fallback for missing booking dates.

    Returns:
        List of 2–5 section dicts. Tourcan: 2 (Summary, Segments). All others: 3 (+ Passengers),
        or +2 if seat charges are present on the invoice.
    """
    rule_set = routing.get("ruleSet", "generic")
    vendor_rules = RULE_SET_MAP.get(rule_set, GENERIC_FLIGHT_RULES)
    section1_schema = (
        _SECTION1_SUMMARY_ONLY if rule_set in _TICKETING_VENDORS else _SECTION1_FULL
    )

    # Tourcan has no passenger details screen; all other vendors do.
    no_pax_screen = rule_set in _NO_PASSENGER_SCREEN_VENDORS
    section3_schema = "" if no_pax_screen else _SECTION3_PASSENGERS
    output_format = _OUTPUT_FORMAT_NO_PASSENGERS if no_pax_screen else _OUTPUT_FORMAT_WITH_PASSENGERS
    section_count = "2" if no_pax_screen else "3"

    # Load real commission docs for AC and WestJet; other vendors extract from invoice directly.
    commission_section = ""
    if rule_set in _COMMISSION_DOC_VENDORS:
        commission_docs = _load_commission_docs()
        if commission_docs:
            commission_section = _COMMISSION_SECTION_TEMPLATE.format(
                commission_docs=commission_docs,
            )

    system = _SYSTEM_PROMPT_TEMPLATE.format(
        vendor_rules=vendor_rules,
        global_rules=GLOBAL_RULES,
        section1_schema=section1_schema,
        section3_schema=section3_schema,
        section_count=section_count,
        output_format=output_format,
        commission_section=commission_section,
    )

    rate_line = f"\n{exchange_rate_note}\n" if exchange_rate_note else ""
    date_line = f"TODAY'S DATE: {today_date}\n" if today_date else ""
    seat_note = "" if no_pax_screen else ", or 5 if seat charges are present"
    user_content = (
        f"VENDOR: {routing.get('vendor', 'Unknown')}\n"
        f"RULE SET: {rule_set}\n"
        f"{date_line}\n"
        f"INVOICE MARKDOWN:\n{markdown}\n"
        f"{rate_line}\n"
        f"Extract all flight data and return the JSON array of sections ({section_count} sections{seat_note})."
    )

    return await call_claude(system, user_content, max_tokens=8192)
