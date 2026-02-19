"""
Currency detection and live exchange rate fetching.

  detect_currency()  — parses the invoice markdown for a 3-letter ISO currency code
  fetch_rate()       — fetches the live CAD rate from frankfurter.app (ECB data, no API key)
  build_rate_note()  — returns a formatted string to inject into extractor user_content,
                       or None if the invoice is already in CAD

frankfurter.app is free, requires no API key, and sources daily rates from the
European Central Bank. httpx is already a project dependency.
"""

import re
from datetime import date

import httpx

FRANKFURTER_URL = "https://api.frankfurter.app/latest"


def detect_currency(markdown: str) -> str:
    """Extract the invoice currency code from Agent 1 markdown output.

    Looks for a line like:  Currency: EUR  or  CURRENCY: USD
    Returns a 3-letter uppercase ISO code, or 'CAD' if not found.
    """
    match = re.search(r"(?i)\bcurrency\s*[:\-]\s*([A-Z]{3})\b", markdown)
    if match:
        return match.group(1).upper()
    return "CAD"


async def fetch_rate(from_currency: str) -> float:
    """Fetch live exchange rate: 1 [from_currency] = X CAD.

    Args:
        from_currency: 3-letter ISO code (e.g. 'EUR', 'USD', 'GBP')

    Returns:
        Float rate, e.g. 1.4823

    Raises:
        httpx.HTTPStatusError: Non-2xx response from API.
        KeyError: CAD not in response (shouldn't happen with this API).
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            FRANKFURTER_URL,
            params={"from": from_currency, "to": "CAD"},
        )
        resp.raise_for_status()
        return resp.json()["rates"]["CAD"]


async def build_rate_note(markdown: str) -> str | None:
    """Detect invoice currency and return a live rate note for injection into extractor prompts.

    Returns None if the invoice is in CAD — no conversion needed.
    Returns a formatted string if non-CAD, e.g.:

      LIVE EXCHANGE RATE: 1 EUR = 1.4823 CAD (fetched 02/19/26)
      Use this exact rate for all CAD conversions and agentRemarks.

    Falls back to None on API failure so the pipeline continues
    (Claude will use its training knowledge as a last resort).
    """
    currency = detect_currency(markdown)
    if currency == "CAD":
        return None

    try:
        rate = await fetch_rate(currency)
    except Exception:
        # API unreachable — return None, Claude falls back to training knowledge
        return None

    today = date.today().strftime("%m/%d/%y")
    return (
        f"LIVE EXCHANGE RATE: 1 {currency} = {rate:.4f} CAD (fetched {today})\n"
        f"Use this exact rate for all CAD conversions and agentRemarks."
    )
