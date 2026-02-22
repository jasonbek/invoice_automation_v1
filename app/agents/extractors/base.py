"""
Shared utilities for all extractor agents.

  GLOBAL_RULES   — formatting rules injected into every extractor prompt
  call_claude()  — makes a Claude API call and parses the JSON array response
"""

import json
import re
import anthropic

GLOBAL_RULES = """\
GLOBAL FORMATTING RULES (apply to every field without exception):
- Dates: MUST be MM/DD/YY (e.g., "08/26/24"). Convert from any other format.
- Times: MUST be 12-hour with AM/PM (e.g., "4:40 PM"). Convert from 24-hour if needed.
- Missing fields: DELETE the key entirely from the output JSON object.
  NEVER use null, undefined, "N/A", or empty string "".
- Currency amounts: If the invoice is in CAD, extract figures exactly as shown.
  If the invoice is NOT in CAD, a LIVE EXCHANGE RATE line will be provided in the input
  (e.g. "LIVE EXCHANGE RATE: 1 EUR = 1.4823 CAD (fetched 02/19/26)").
  Use that exact rate to convert totalBase (and commission where applicable) to CAD,
  and populate the agentRemarks field as follows:
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
- Promotions and savings: If the invoice mentions any special deal, promotional discount,
  loyalty reward, price reduction, or savings amount, ALWAYS include it in the
  client-facing remarks field for that booking type:
    · flights, tours, cruises → invoiceRemarks
    · hotels                  → notesForClient
  Example: "Special deal applied: 15% off (CA $224.91 savings)"
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

    message = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}],
    )

    raw = message.content[0].text.strip()

    # Strip accidental code fences (```json ... ```)
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```\s*$", "", raw)
    raw = raw.strip()

    parsed = json.loads(raw)

    if not isinstance(parsed, list):
        raise ValueError(f"Expected JSON array from extractor, got: {type(parsed)}")

    return parsed
