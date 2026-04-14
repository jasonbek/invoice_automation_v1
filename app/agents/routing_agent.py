"""
Agent 2: Routing Specialist

Reads the invoice Markdown and classifies:
  - vendor  (normalized official name)
  - ruleSet (key used to load vendor-specific rules in extractors)
  - bookingTypes (list — a single invoice may have flights + tour)
  - serviceFeeIncluded (true if form service_fee > 0)

Returns a plain dict (parsed from the model's JSON output).
"""

import asyncio
import json
import re
import anthropic

SYSTEM_PROMPT = """\
You are a booking classifier for a travel agency using ClientBase Online software.
Analyze the invoice Markdown and return ONLY a JSON object — no prose, no code fences.

═══════════════════════════════════════════════
VENDOR NORMALIZATION TABLE
═══════════════════════════════════════════════
Use the HINT from the form as a starting point, but trust the invoice content.

| Official Name       | Aliases / Triggers                                                |
|---------------------|-------------------------------------------------------------------|
| Air Canada Internet | Air Canada, AC, AirCan                                            |
| Westjet Internet    | West Jet, Westjet, WJ                                             |
| Expedia TAAP        | Expedia, TAAP                                                     |
| BedsonLine          | BedsOnline, Beds Online, Beds On Line, BedsonLine (hotel)         |
| Intair              | Travel Brands — ONLY when booking type is Flight                  |
| Travel Brands       | Travel Brands — when booking type is Tour or Land                 |
| ADX                 | ADX, or Intair when invoice has an explicit "COMMISSION" line     |
| Manulife Insurance  | Any insurance policy document                                     |
| Viator on Line      | Viator (bookingType: day_tour — NOT tour)                         |
| Daytrip             | Daytrip, mydaytrip.com (bookingType: day_tour)                    |
| Tourcan Vacations   | TOURCAN VACATIONS, Tourcan                                        |
| VIA Rail            | VIA, VIA Rail Canada                                              |
| Amtrak              | Amtrak, National Railroad Passenger                               |
| Eurostar            | Eurostar                                                          |
| Rail Europe Inc     | Eurail, Rail Europe, The Trainline                                |
| Service Fee         | Internal agency fee invoice                                       |

RULE SET KEY MAPPING (must use these exact strings):
  air_canada    → Air Canada Internet
  westjet       → Westjet Internet
  adx_intair    → ADX / Intair (explicit COMMISSION line present on invoice)
  expedia       → Expedia TAAP
  bedsonline    → BedsonLine
  travel_brands → Travel Brands / Intair (tour bookings)
  viator        → Viator on Line
  daytrip       → Daytrip
  manulife      → Manulife Insurance
  tourcan       → Tourcan Vacations
  generic       → all others (including all rail vendors — VIA Rail, Amtrak, Eurostar, Rail Europe)

ADX vs INTAIR DECISION:
  - Invoice header says "Intair" or "Travel Brands" AND has an explicit line labelled
    "COMMISSION" with a dollar amount → vendor = "ADX", ruleSet = "adx_intair"
  - Invoice header says "Travel Brands" + booking is a Tour (no COMMISSION line)
    → vendor = "Travel Brands", ruleSet = "travel_brands"
  - Invoice header says "Travel Brands" + booking is a Flight (no COMMISSION line)
    → vendor = "Intair", ruleSet = "travel_brands"

═══════════════════════════════════════════════
BOOKING TYPE DETECTION SIGNALS
═══════════════════════════════════════════════
- flight       : airline ticket, PNR locator, flight segments with departure/arrival cities and times
- tour         : multi-day land package, tour code, land arrangements, accommodation + guided activities
                 (Travel Brands, Intair, generic tour operators — NOT Viator)
- day_tour     : single-day excursion, shore excursion, or day activity from Viator on Line,
                 Daytrip, or any other day-tour operator. Use ruleSet "viator" for Viator,
                 "daytrip" for Daytrip, otherwise "generic". Do NOT force the vendor name to
                 "Viator on Line" for non-Viator day tours.
- hotel        : accommodation-only booking, check-in/check-out dates, no flights included
- cruise       : ship name, cabin number, embarkation/debarkation ports, cruise line
- insurance    : policy number, premium amount, coverage start/end dates
- new_traveller: customer profile with personal contact details; no booking data
- rail         : train ticket, rail reservation number, train segments with departure/arrival stations,
                 seat/car/coach number, rail pass, VIA Rail, Amtrak, Eurostar, Eurail, Rail Europe, SNCF
- seat_selection: standalone seat selection fee invoice — contains seat assignment charges without a full
                 flight itinerary; keywords like "seat selection", "seat fee", "seat charge", assigned
                 seat numbers with dollar amounts, no PNR segments or flight times

IMPORTANT: A single invoice may contain BOTH flights and a tour (air + land package).
List ALL detected booking types in the bookingTypes array.

═══════════════════════════════════════════════
OUTPUT FORMAT (strict JSON — no prose, no fences)
═══════════════════════════════════════════════
{
  "vendor": "<Official Name from table above>",
  "ruleSet": "<rule set key>",
  "bookingTypes": ["<type1>"],
  "serviceFeeIncluded": <true or false>
}\
"""


async def run(markdown: str, vendor_hint: str, booking_type_hint: str) -> dict:
    """Classify vendor and booking types from invoice Markdown.

    Args:
        markdown: Extracted invoice content from Agent 1.
        vendor_hint: Vendor name submitted via the n8n form.
        booking_type_hint: Optional booking type hint from the form.

    Returns:
        Dict with keys: vendor, ruleSet, bookingTypes, serviceFeeIncluded
    """
    client = anthropic.AsyncAnthropic(max_retries=6)

    user_content = (
        f'VENDOR HINT from form: "{vendor_hint}"\n'
        f'BOOKING TYPE HINT from form: "{booking_type_hint}"\n\n'
        f"INVOICE MARKDOWN:\n{markdown}\n\n"
        "Return only the JSON classification object."
    )

    app_retries = 8
    for attempt in range(app_retries + 1):
        try:
            message = await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=512,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
            )
            break
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as e:
            if attempt < app_retries:
                delay = min(30 * (2 ** attempt), 300)
                print(f"[routing_agent] {type(e).__name__}, retrying in {delay}s "
                      f"(attempt {attempt + 1}/{app_retries})")
                await asyncio.sleep(delay)
                continue
            raise
        except anthropic.APIStatusError as e:
            if e.status_code == 529 and attempt < app_retries:
                delay = min(30 * (2 ** attempt), 300)
                print(f"[routing_agent] Anthropic overloaded (529), retrying in {delay}s "
                      f"(attempt {attempt + 1}/{app_retries})")
                await asyncio.sleep(delay)
                continue
            raise

    raw = message.content[0].text.strip()

    # Strip accidental code fences
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```\s*$", "", raw)
    raw = raw.strip()

    return json.loads(raw)
