"""
Agent 3g: Rail Extractor

Handles rail bookings from any operator (VIA Rail, Amtrak, Eurostar, Rail Europe, etc.).
Rail bookings are entered in ClientBase Online using the dedicated Rail screens.

Output:
  - 1 × Screen 1 (Summary) — full booking financials
  - N × Screen 2 (Details) — one section per rail segment (one train leg = one section)
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

Ticket numbers: Each segment may carry its own ticket number. If multiple passengers on
  the same segment each have a distinct ticket number, put the primary booking reference
  in recordLocator and list all per-passenger ticket numbers in agentRemarks for that
  segment (e.g. "Ticket 1234567 – J. Smith / Ticket 1234568 – M. Smith").

Multi-document synthesis: Rail bookings are often submitted as multiple files — a supplier
  booking summary plus one or more individual ticket PDFs. All files are combined into
  a single markdown before reaching you. These documents are complementary, not duplicates:

  Booking summary typically contains:
    - Booking/reservation date, total fare, taxes, booking reference
    - Passenger names, overall travel dates

  Individual ticket PDFs typically contain:
    - Train number, coach/car, seat number or class
    - Departure and arrival times (often absent from the summary)
    - Per-segment or per-passenger ticket number
    - Station platform or terminal details

  Cross-reference ALL source material to build complete Screen 2 data for every segment.
  Do not leave trainNumber, startTime, endTime, miscellaneous, or recordLocator empty
  if the data appears anywhere in the combined content.\
"""

RULE_SET_MAP = {
    "generic": GENERIC_RAIL_RULES,
}

# ── System prompt ──────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a rail booking data extraction specialist for a travel agency (ClientBase Online).
Rail bookings are entered using the dedicated Rail screens in ClientBase.
Extract data from the Markdown invoice and return ONLY a JSON array of section objects.

{vendor_rules}

{global_rules}

═══════════════════════════════════════════════
SCHEMA — 1 + N sections required
═══════════════════════════════════════════════

### SECTION 1 — Rail Screen 1 (Summary)
One section only. Covers the full booking overview and financials.

{{
  "reservationDate": "MM/DD/YY",
  "vendorName": "Rail operator name (e.g. VIA Rail, Amtrak, Eurostar, Rail Europe)",
  "confirmationNumber": "Primary booking/trip reference — join multiples with '/'",
  "duration": <integer — total trip days from first departure to last arrival>,
  "noofpax": <integer — number of passengers>,
  "noofunits": <integer — number of rail segments — MUST equal the number of Screen 2 sections you output>,
  "tripType": "Domestic | Transborder | International",
  "includegst": "Include GST/HST | Do Not Include GST/HST",
  "totalBase": <total rail fare, 2 decimal places>,
  "totalTax": <taxes and fees if shown on invoice, 2 decimal places — use "" if none>,
  "totalCommission": "0% — unless invoice shows an explicit commission figure",
  "invoiceRemarks": "Client-facing notes: promotions, savings, loyalty rewards, inclusions — use \"\" if none",
  "agentRemarks": "Currency conversion block — REQUIRED if invoice is not in CAD; use \"\" if CAD"
}}

tripType — determined by the stations on the itinerary:
  Domestic      — all stations within Canada
  Transborder   — any Canada <-> USA station, no overseas
  International — any station outside Canada and USA

includegst:
  Include GST/HST     — Canadian GST/HST is included in the fare shown
  Do Not Include GST/HST — GST/HST is not itemised or is zero

### SECTION 2 — Rail Screen 2 (Details) — ONE PER RAIL SEGMENT
A segment = one train leg (one departure city, one arrival city).
Connecting services with a different train number = separate segments.
If a passenger does NOT change trains at an intermediate stop, that is still ONE segment.
Repeat this section for EACH segment. A 3-segment booking produces 3 Screen 2 sections.

{{
  "serviceProviderName": "Rail operator for this segment (e.g. VIA Rail, Amtrak, Eurostar)",
  "trainNumber": "Train number for this segment. Look for it: (1) under a 'ROUTE' label on the ticket, (2) on the line directly below or beside the departure city, often formatted as 'SERVICE NAME NNNN - N. CLASS' (e.g. 'INTERCITÉS 3629 - 1. CLASS' → trainNumber is '3629', 'TGV INOUI 8318 - 1. CLASS' → '8318'). Extract the numeric train identifier only — strip the service brand name and class suffix. The train number is NEVER the same as the booking reference, locator, or ticket number.",
  "departCityCode": "Station or city code — see CITY CODE RULES below",
  "departCityName": "Departure city name (e.g. Vancouver, London, New York)",
  "departTerminal": "Departure station full name (e.g. Pacific Central Station, London St Pancras International)",
  "startDate": "MM/DD/YY",
  "startTime": "H:MM AM/PM",
  "arriveCityCode": "Station or city code — see CITY CODE RULES below",
  "arriveCityName": "Arrival city name",
  "arriveTerminal": "Arrival station full name",
  "endDate": "MM/DD/YY",
  "endTime": "Arrival time in H:MM AM/PM format. On ticket PDFs this often appears alongside the arrival station name, near the seat/coach details, or as the second time in a departure→arrival time pair on the ticket. Check every ticket PDF carefully — do not leave this blank if a time appears anywhere near the arrival station for this segment. use "" only if genuinely absent from all source documents.",
  "recordLocator": "Per-segment ticket or booking reference. If no per-segment reference exists, repeat the master confirmationNumber.",
  "miscellaneous": "Seat number, travel class, car/coach number — use \"\" if none",
  "description": "Short segment description (e.g. 'VIA Rail — Vancouver to Winnipeg (The Canadian)')",
  "clientFeedback": "Client-facing information for this segment: check-in instructions, baggage allowance, onboard amenities, meal service, cancellation policy. Plain text with line breaks. NO pricing or financial figures.",
  "agentRemarks": "Per-segment agent notes only (e.g. per-passenger ticket numbers if multiple) — use \"\" if none"
}}

═══════════════════════════════════════════════
CITY CODE RULES (departCityCode and arriveCityCode)
═══════════════════════════════════════════════
Rail stations do not always use standardized codes. Apply this priority:
  1. If the invoice explicitly shows a station or city code, use it verbatim.
  2. If no code appears on the invoice, use your training knowledge to determine the
     standard code for that city (IATA city code, UIC station code, or the code used by
     that rail operator). Common examples:
       YVR = Vancouver   YYC = Calgary   YEG = Edmonton
       YWG = Winnipeg    YTO = Toronto   YUL = Montreal   YHZ = Halifax
       NYP = New York Penn Station       WAS = Washington DC
       CHI = Chicago     LAX = Los Angeles
       PAD = London Paddington           STP = London St Pancras
       CDG = Paris       AMS = Amsterdam  BRU = Brussels
  3. If you are genuinely uncertain of the correct code, output "" (empty string).

IMPORTANT: departCityCode and arriveCityCode MUST always be present as keys in the output.
Use "" rather than omitting the key. This overrides the standard missing-field delete rule
for these two fields only — the CBO macro reads the property directly and requires the key.
Never invent or guess a code you are not confident about.

═══════════════════════════════════════════════
OUTPUT FORMAT
═══════════════════════════════════════════════
[
  {{"sectionTitle": "Rail Screen 1 (Summary)", "data": {{ ... }}}},
  {{"sectionTitle": "Rail Screen 2 (Details)", "data": {{ ... }}}},
  {{"sectionTitle": "Rail Screen 2 (Details)", "data": {{ ... }}}}
]

IMPORTANT: Use "Rail Screen 2 (Details)" as the sectionTitle for EVERY segment section,
even when there are multiple. Each segment is a separate JSON object in the array.
The number of Screen 2 sections MUST equal noofunits in Screen 1.
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
        List of 1 + N section dicts: Rail Screen 1 Summary, then one Rail Screen 2 Details
        per segment.
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
        "Extract all rail booking data and return the JSON array. "
        "Remember: one Screen 2 section per rail segment — separate train legs are separate sections."
    )

    return await call_claude(system, user_content, max_tokens=8192)
