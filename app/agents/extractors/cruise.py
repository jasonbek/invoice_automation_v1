"""
Agent 3d: Cruise Extractor

Outputs 2 sections: Summary, Details.
Cruise invoices often include itinerary text blocks — preserve these in clientFeedback.
Non-CAD invoices must have agentRemarks with conversion details.
"""

from app.agents.extractors.base import GLOBAL_RULES, call_claude

_SYSTEM_PROMPT = f"""\
You are a cruise booking data extraction specialist for a travel agency (ClientBase Online).
Extract data from the Markdown invoice and return ONLY a JSON array of section objects.

{GLOBAL_RULES}

FINANCIAL PLACEMENT RULE: ALL financial figures — totalBase, totalTax, totalCommission,
deposits, port charges, gratuities, discounts, savings, fare breakdowns — belong on
SCREEN 1 ONLY. Screen 2 (Cruise Details) must contain ZERO currency amounts and ZERO
financial figures. Do not leak pricing into clientFeedback, description, or any other
Screen 2 field.

CURRENCY NOTE: If the invoice is NOT in CAD, convert totalBase to CAD using the best
available exchange rate and populate agentRemarks:
  DEPOSIT PAID: $[CAD amount] CAD
  COMMISSION: [raw amount] [currency]
  Invoiced in [currency] by Supplier
  Amounts in CB Converted to CAD on [MM/DD/YY] @ rate of 1 [currency] : [rate] CAD

═══════════════════════════════════════════════
SCHEMA — 2 sections required
═══════════════════════════════════════════════

### SECTION 1 — Cruise Summary
{{
  "reservationDate": "MM/DD/YY",
  "vendorName": "Cruise line or booking vendor name",
  "confirmationNumber": "String",
  "duration": "Number of nights (string)",
  "noofpax": "String (number of passengers)",
  "noofunit": "String (number of cabins)",
  "tripType": "International | Transborder | Domestic",
  "totalBase": "Amount in CAD, 2 decimal places",
  "totalTax": "String",
  "totalCommission": "String (in supplier currency)",
  "finalpymntduedate": "MM/DD/YY",
  "invoiceRemarks": "Client-facing notes, discounts, included items",
  "agentRemarks": "Currency conversion and financial notes"
}}

### SECTION 2 — Cruise Details
{{
  "shipName": "String",
  "startDate": "MM/DD/YY (embarkation date)",
  "endDate": "MM/DD/YY (debarkation date)",
  "category": "Cabin category code",
  "deck": "String",
  "cabinNumber": "String",
  "diningTime": "String",
  "bedding": "String",
  "description": "Stateroom description",
  "clientFeedback": "Chronological day-by-day cruise itinerary as a plain text block, built ONLY from explicit information in the supplier document. Do NOT hallucinate shore excursions or activities. Do NOT include any financial figures — those belong on Screen 1 ONLY.\\n\\nBegin with this exact header on its own lines:\\n  Itinerary at a glance\\n  ----------------------\\n\\nThen one entry per day in this exact pipe-delimited format (four columns):\\n\\nDay [N] | MM/DD/YYYY | [Location] | [Headline]\\n\\nDAY NUMBERING — use the supplier document's OWN 'Day N' numbering exactly as printed. Do NOT renumber. If the document's numbering skips, the output MUST reflect that gap.\\n\\nHEADLINE PRIORITY (use the highest-priority source available; append additional same-day items with '; '):\\n  1. PER-DAY HEADING / PORT-CALL text — itinerary table rows or headings like 'Day N: Civitavecchia (Rome) - Embark' or bold port lines. Use the port/heading text VERBATIM (preserve supplier wording).\\n  2. Sea days as the document labels them: 'At Sea'.\\n  3. Embark/Debark annotations from the document.\\n\\nOWN ARRANGEMENTS RULE — strict:\\n- If a heading or range says 'OWN ARRANGEMENTS', 'Own arrangements', or 'Own arrangement', SKIP those days ENTIRELY — no row emitted, leave a gap in the day numbering.\\n- NEVER substitute 'At Sea' or 'At leisure' for a skipped Own-Arrangements day. A skipped day produces NO row.\\n\\nOTHER RULES:\\n- Every date, port, and ship activity MUST come from the supplier document. Never invent.\\n- NEVER fabricate days that are not present in the document.\\n- No times, no prices, no currency amounts.\\n\\nAt the very end of the block, append this exact disclaimer on its own line (blank line before it):\\n\\nPlease refer to the supplier documentation for further detail; it takes precedence over this outline in the event of any discrepancies."
}}

═══════════════════════════════════════════════
OUTPUT FORMAT
═══════════════════════════════════════════════
[
  {{"sectionTitle": "Cruise Screen 1 (Summary)", "data": {{ ... }}}},
  {{"sectionTitle": "Cruise Screen 2 (Details)", "data": {{ ... }}}}
]
Return ONLY the JSON array. No prose, no markdown fences.\
"""


async def run(
    markdown: str,
    routing: dict,
    exchange_rate_note: str | None = None,
    today_date: str = "",
    source_blocks: list[dict] | None = None,
) -> list[dict]:
    """Extract cruise sections from invoice Markdown.

    When source_blocks are provided, Sonnet re-reads the raw supplier document
    directly — needed for the port-by-port "Itinerary at a glance" block, which
    Agent 1's LABEL:value filter strips out.
    """
    rate_line = f"\n{exchange_rate_note}\n" if exchange_rate_note else ""
    date_line = f"TODAY'S DATE: {today_date}\n" if today_date else ""

    instruction_text = (
        f"VENDOR: {routing.get('vendor', 'Unknown')}\n"
        f"{date_line}\n"
        f"COMPACT EXTRACT (from Agent 1 — cross-reference for confirmation numbers, "
        f"pricing, passenger names):\n{markdown}\n"
        f"{rate_line}\n"
        "The attached supplier document above is the authoritative source for the "
        "port-by-port itinerary (clientFeedback). Use it for the 'Itinerary at a glance' "
        "block. Use the compact extract above for confirming fields on Screen 1. "
        "Return the JSON array of 2 section objects."
    )

    if source_blocks:
        user_content: str | list[dict] = list(source_blocks) + [
            {"type": "text", "text": instruction_text}
        ]
    else:
        user_content = instruction_text

    return await call_claude(_SYSTEM_PROMPT, user_content, max_tokens=4096)
