"""
Agent 3f: Service Fee Extractor

Service fees are generated primarily from form data, not the invoice.
Uses a small Claude call only to determine the passenger count from the Markdown.
Outputs 2 sections: Summary, Details.
"""

from datetime import date
from app.agents.extractors.base import call_claude

_CONTEXT_PROMPT_SYSTEM = """\
You are a data extraction assistant. Read the invoice Markdown and return ONLY a JSON object
with these three fields:

  noofpax   — total number of travellers/passengers (integer, default 1 if unknown)
  startDate — first departure / check-in / tour start date in MM/DD/YY format
  endDate   — last arrival / check-out / tour end date in MM/DD/YY format

Omit startDate or endDate if they cannot be determined from the invoice.

Example output:
{"noofpax": 2, "startDate": "08/26/24", "endDate": "09/02/24"}\
"""


async def _get_invoice_context(markdown: str) -> dict:
    """Single Claude call to extract pax count and trip dates from invoice."""
    import json, re, anthropic

    client = anthropic.AsyncAnthropic()
    message = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=64,
        system=_CONTEXT_PROMPT_SYSTEM,
        messages=[{"role": "user", "content": markdown}],
    )
    raw = message.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```\s*$", "", raw)
    return json.loads(raw.strip())


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
    context = await _get_invoice_context(markdown)

    noofpax = str(context.get("noofpax", 1))
    start_date = context.get("startDate", today)
    end_date = context.get("endDate", today)

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
                "chargedAs": "Total",
                "totalBase": f"{service_fee_amount:.2f}",
                "commissionPercentage": "100",
                "clientGstRate": "5",
            },
        },
        {
            "sectionTitle": "Service Fee Screen 2",
            "data": {
                "serviceProviderName": "Service Fee",
                "startDate": start_date,
                "endDate": end_date,
                "description": "Agency Planning Fee",
            },
        },
    ]
