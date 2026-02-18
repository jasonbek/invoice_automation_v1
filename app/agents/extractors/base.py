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
- Currency amounts: Extract the raw figure exactly as shown on the invoice.
  Do NOT convert currencies unless the schema explicitly requires CAD conversion.
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
