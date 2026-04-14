"""
Shared utilities for all extractor agents.

  GLOBAL_RULES   — formatting rules injected into every extractor prompt
  call_claude()  — makes a Claude API call and parses the JSON array response
"""

import asyncio
import json
import re
import anthropic

GLOBAL_RULES = """\
GLOBAL FORMATTING RULES (apply to every field without exception):
- Dates: MUST be MM/DD/YY (e.g., "08/26/24"). Convert from any other format.
- Times: MUST be 12-hour with AM/PM (e.g., "4:40 PM"). Convert from 24-hour if needed.
- Missing fields: Use "" (empty string) for any field where no value is available.
  NEVER omit a key. NEVER use null, undefined, "N/A", "?", "??", or any other placeholder.
  If you are uncertain about a value, use "" — never use a question mark.
- Currency amounts: If the invoice is in CAD, extract figures exactly as shown.
  If the invoice is NOT in CAD, a LIVE EXCHANGE RATE line will be provided in the input
  (e.g. "LIVE EXCHANGE RATE: 1 EUR = 1.4823 CAD (fetched 02/19/26)").
  Use that exact rate to convert totalBase to CAD.
  Do NOT convert commission — always record commission in the original invoice currency.
  Populate the agentRemarks field as follows:
    DEPOSIT PAID: $[CAD amount] CAD
    COMMISSION: [raw amount] [currency]
    Invoiced in [currency] by Supplier
    Amounts in CB Converted to CAD on [MM/DD/YY] @ rate of 1 [currency] : [rate] CAD
  This applies to ALL booking types (flight, tour, hotel, cruise, etc.).
- Accented characters: Replace with their unaccented ASCII equivalent in ALL string fields.
  Examples: é→e, è→e, ê→e, ë→e, à→a, â→a, ô→o, î→i, û→u, ç→c, ü→u, ñ→n, etc.
  Example: "Hôtel de Varenne" → "Hotel de Varenne"
- Booking/reservation date: If no booking or reservation date appears on the invoice,
  use the TODAY'S DATE value provided in the input. Format it as MM/DD/YY.
- Client-facing remarks — currency disclosure + financial summary (ALL booking types, ALL currencies including CAD):
  ALWAYS prepend the following block to the invoiceRemarks field on Screen 1
  (applies to EVERY booking type — flights, tours, cruises, day tours, rail,
  hotels, insurance). The financial block NEVER belongs on Screen 2 (details).
  For hotels specifically: the Screen 2 notesForClient field is ONLY for hotel
  contact info (address, phone, email) and 'Due at property' items — NO
  financial summary, NO deposit, NO totals there.
    Payments are in [currency code, e.g. USD/EUR/CAD]
    The amount shown below is in CAD
    Deposit: $[amount] [currency]
    Total: $[amount] [currency]
    Amount owing: $[amount] [currency]
  All three financial lines (Deposit / Total / Amount owing) use the SUPPLIER'S
  ORIGINAL INVOICE CURRENCY — NOT converted to CAD. If the invoice IS in CAD,
  the currency is CAD.
  If no deposit is shown, write "Deposit: $0 [currency]".
  If the amount owing is not explicitly shown, compute it as Total − Deposit.
  Do NOT include the exchange rate in this block — rate details stay in agentRemarks only.
  Do NOT add any itinerary, booking details, day-by-day info, or routing info here —
  those belong ONLY on Screen 2 in clientFeedback / clientItinerary.
- Promotions and savings: If the invoice mentions any special deal, promotional discount,
  loyalty reward, price reduction, or savings amount, ALWAYS include it in the
  Screen 1 invoiceRemarks field for every booking type (including hotels).
  Example: "Special deal applied: 15% off (CA $224.91 savings)"
- Supplier name normalization: When writing vendor / serviceProviderName / supplier
  fields, use the exact legal name below whenever you detect a match (case-insensitive,
  any variation of the short name):
    · "Insight Vacations" → "Insight Vacations (Canada) Ltd"
    · "Beds Online" / "BedsOnline" / "Beds On Line" → "BedsonLine"
- Output: Return ONLY the JSON array described in the schema. No prose, no code fences.\
"""


async def call_claude(
    system_prompt: str,
    user_content: str,
    max_tokens: int = 4096,
) -> list[dict]:
    """Make a Claude API call and parse the JSON array response.

    Args:
        system_prompt: The focused extractor system prompt.
        user_content:  Invoice markdown + routing context.
        max_tokens:    Token budget for the response.

    Returns:
        Parsed list of section dicts, each with 'sectionTitle' and 'data'.

    Raises:
        json.JSONDecodeError: If Claude returns malformed JSON.
        ValueError: If Claude returns something other than a JSON array.
    """
    # max_retries=6 gives ~60s total backoff — handles Tier 1 rate limits automatically
    client = anthropic.AsyncAnthropic(max_retries=6)

    # Application-level retries for transient failures (overload, connection, timeout).
    # Exponential backoff: 30s → 60s → 120s → 240s (capped at 300s).
    app_retries = 8
    for attempt in range(app_retries + 1):
        try:
            message = await client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_content}],
            )
            break  # success
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as e:
            if attempt < app_retries:
                delay = min(30 * (2 ** attempt), 300)
                print(f"[call_claude] {type(e).__name__}, retrying in {delay}s "
                      f"(attempt {attempt + 1}/{app_retries})")
                await asyncio.sleep(delay)
                continue
            raise
        except anthropic.APIStatusError as e:
            if e.status_code == 529 and attempt < app_retries:
                delay = min(30 * (2 ** attempt), 300)
                print(f"[call_claude] Anthropic overloaded (529), retrying in {delay}s "
                      f"(attempt {attempt + 1}/{app_retries})")
                await asyncio.sleep(delay)
                continue
            raise

    raw = message.content[0].text.strip()

    # 1. If Claude wrapped output in a code fence, extract its contents
    fence_match = re.search(r"```(?:json)?\s*\n?([\s\S]*?)\n?\s*```", raw)
    if fence_match:
        raw = fence_match.group(1).strip()
    else:
        # 2. If Claude added prose before the JSON array, skip to the first '['
        bracket = raw.find("[")
        if bracket > 0:
            raw = raw[bracket:]
        raw = raw.strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        safe_preview = raw[:500].encode("ascii", "replace").decode("ascii")
        print(f"[call_claude] JSON parse failed: {e}")
        print(f"[call_claude] stop_reason={message.stop_reason!r}  content_length={len(raw)}")
        print(f"[call_claude] raw response (first 500 chars): {safe_preview!r}")
        raise

    if not isinstance(parsed, list):
        raise ValueError(f"Expected JSON array from extractor, got: {type(parsed)}")

    return parsed
