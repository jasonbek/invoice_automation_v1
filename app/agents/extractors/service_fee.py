"""
Agent 3f: Service Fee Extractor

Service fees are generated primarily from form data, not the invoice.
Uses a small Claude call only to determine the passenger count from the Markdown.
Outputs 2 sections: Summary, Details.
"""

from datetime import date
from app.agents.extractors.base import call_claude

_PAX_PROMPT_SYSTEM = """\
You are a data extraction assistant. Read the invoice Markdown and return ONLY a JSON object
with one field: the total number of travellers/passengers mentioned.

Output exactly:
{"noofpax": <integer>}

If you cannot determine the count, return: {"noofpax": 1}\
"""


async def _get_pax_count(markdown: str) -> str:
    """Quick Claude call to extract passenger count from invoice."""
    import json, re, anthropic

    client = anthropic.AsyncAnthropic()
    message = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=64,
        system=_PAX_PROMPT_SYSTEM,
        messages=[{"role": "user", "content": markdown}],
    )
    raw = message.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```\s*$", "", raw)
    result = json.loads(raw.strip())
    return str(result.get("noofpax", 1))


async def run(markdown: str, routing: dict, service_fee_amount: float) -> list[dict]:
    """Generate service fee sections from form data + invoice context.

    Args:
        markdown:           Invoice Markdown (used only to infer passenger count).
        routing:            Routing result (not used directly here).
        service_fee_amount: Dollar amount from the n8n form service_fee field.

    Returns:
        List of 2 section dicts (Service Fee Summary and Details).
    """
    today = date.today().strftime("%m/%d/%y")
    noofpax = await _get_pax_count(markdown)

    return [
        {
            "sectionTitle": "Service Fee Screen 1",
            "data": {
                "reservationDate": today,
                "vendorName": "Service Fee",
                "duration": "1",
                "noofpax": noofpax,
                "noofunits": "1",
                "tripType": "Domestic",
                "chargedAs": "Per Booking",
                "totalBase": f"{service_fee_amount:.2f}",
                "commissionPercentage": "100",
                "clientGstRate": "5",
            },
        },
        {
            "sectionTitle": "Service Fee Screen 2",
            "data": {
                "serviceProviderName": "Service Fee",
                "startDate": today,
                "endDate": today,
                "description": "Agency Planning Fee",
            },
        },
    ]
