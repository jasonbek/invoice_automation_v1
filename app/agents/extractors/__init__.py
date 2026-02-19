"""
Extractor orchestrator.

run_all() routes to the correct extractor(s) based on the routing result and
runs them in parallel using asyncio.gather().

Each extractor returns a list of section dicts:
  [{"sectionTitle": "...", "data": {...}}, ...]

All section lists are flattened into a single ordered list.

Currency handling: a live exchange rate is fetched ONCE here (before the gather)
and injected into every extractor call. This keeps rate-fetching in one place.
"""

import asyncio

from app.agents.extractors import (
    flight as flight_ext,
    tour as tour_ext,
    hotel as hotel_ext,
    cruise as cruise_ext,
    insurance as insurance_ext,
    service_fee as sf_ext,
    new_traveller as nt_ext,
)
from app.agents.extractors.currency import build_rate_note

# Maps booking type strings (from routing agent) to extractor functions
EXTRACTOR_MAP = {
    "flight": flight_ext.run,
    "tour": tour_ext.run,
    "hotel": hotel_ext.run,
    "cruise": cruise_ext.run,
    "insurance": insurance_ext.run,
    "new_traveller": nt_ext.run,
}


async def run_all(markdown: str, routing: dict, service_fee_amount: float) -> list[dict]:
    """Run all required extractors in parallel; return flat ordered list of sections.

    Args:
        markdown:           Full invoice Markdown from Agent 1.
        routing:            Classification result from Agent 2.
        service_fee_amount: Service fee dollar amount from the form (0 = no fee).

    Returns:
        Flat list of section dicts sorted by booking type order, then service fee last.
    """
    # Fetch live exchange rate once — shared by all extractors in this run.
    # Returns None if invoice is in CAD (no conversion needed).
    exchange_rate_note = await build_rate_note(markdown)

    tasks = []

    for booking_type in routing.get("bookingTypes", []):
        extractor_fn = EXTRACTOR_MAP.get(booking_type)
        if extractor_fn:
            tasks.append(extractor_fn(markdown, routing, exchange_rate_note))
        else:
            # Unknown booking type — skip with a warning section
            tasks.append(
                _unknown_type_section(booking_type)
            )

    # Service fee always appended last if amount > 0
    if service_fee_amount > 0:
        tasks.append(sf_ext.run(markdown, routing, service_fee_amount))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    sections = []
    for result in results:
        if isinstance(result, Exception):
            sections.append(
                {
                    "sectionTitle": "Extraction Error",
                    "data": {"error": str(result)},
                }
            )
        else:
            sections.extend(result)

    return sections


async def _unknown_type_section(booking_type: str) -> list[dict]:
    """Placeholder section for unrecognised booking types."""
    return [
        {
            "sectionTitle": f"Unknown Booking Type: {booking_type}",
            "data": {"error": f"No extractor registered for type '{booking_type}'"},
        }
    ]
