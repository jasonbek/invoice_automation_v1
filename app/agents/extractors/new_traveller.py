"""
Agent 3g: New Traveller Profile Extractor

Outputs 4 sections:
  Section 1 — Contact info (one object for the household)
  Section 2 — Traveller 1 demographics
  Section 3 — Traveller 2 demographics (if applicable)
  Section 4 — Preferences (plain text block)

Key formatting rules:
  - state: 2-letter province/state code (ON, BC, NY, etc.)
  - citizenship: 2-letter ISO country code (CA, US, UK, etc.)
  - birthMonth: full month name (e.g., "July")
  - phoneAreaCode: 3 digits only
  - phoneNumber: 7 digits only (no area code, no dashes)
"""

from app.agents.extractors.base import GLOBAL_RULES, call_claude

_SYSTEM_PROMPT = f"""\
You are a new traveller profile extraction specialist for a travel agency (ClientBase Online).
Extract data from the profile document and return ONLY a JSON array of section objects.

{GLOBAL_RULES}

FORMATTING RULES SPECIFIC TO PROFILES:
- state / province: 2-letter code only (e.g., "ON", "BC", "AB", "NY")
- citizenship: 2-letter ISO country code (e.g., "CA", "US", "GB")
- birthMonth: full month name (e.g., "July", "December")
- birthDay: 1 or 2 digit day number (e.g., "7", "23")
- birthYear: 4-digit year (e.g., "1978")
- phoneAreaCode: exactly 3 digits
- phoneNumber: exactly 7 digits (no area code, no spaces, no dashes)
- firstName for Section 1 (Contact): if couple, format as "John & Jane"

═══════════════════════════════════════════════
SCHEMA — 4 sections required
═══════════════════════════════════════════════

### SECTION 1 — Contact Information (one object for the household)
{{
  "lastName": "String",
  "firstName": "String (e.g., 'John & Jane' for couple, 'John' for individual)",
  "middlenames": "String",
  "address1": "Street address line 1",
  "address2": "Street address line 2 (apt/suite if on separate line)",
  "aptSuite": "Apartment or suite number",
  "zipCode": "Postal or ZIP code",
  "city": "City name",
  "state": "2-letter province/state code",
  "country": "Country name",
  "phoneAreaCode": "3 digits",
  "phoneNumber": "7 digits"
}}

### SECTION 2 — Traveller 1 Demographics
{{
  "lastName": "String",
  "firstName": "String",
  "middlenames": "String",
  "citizenship": "2-letter ISO code",
  "birthMonth": "Full month name",
  "birthDay": "1–2 digit day",
  "birthYear": "4-digit year",
  "email": "String"
}}

### SECTION 3 — Traveller 2 Demographics (if a second traveller exists; omit section entirely if only one traveller)
{{
  "lastName": "String",
  "firstName": "String",
  "middlenames": "String",
  "citizenship": "2-letter ISO code",
  "birthMonth": "Full month name",
  "birthDay": "1–2 digit day",
  "birthYear": "4-digit year",
  "email": "String"
}}

### SECTION 4 — Preferences (plain text string — preserve formatting with line breaks)
Format exactly as:
Emergency Contact
-----------------
Name: [Name]
Phone: [Phone]
Email: [Email]

Travel Preferences
------------------
Seating: [Value]
Class: [Value]
Dietary: [Value]

Destinations of Interest
------------------------
[List]

Loyalty Numbers
---------------
[Airline/Program]: [Number]

═══════════════════════════════════════════════
OUTPUT FORMAT
═══════════════════════════════════════════════
Always include Sections 1, 2, and 4.
Include Section 3 ONLY if a second traveller is present.

[
  {{"sectionTitle": "Profile Screen 1 (Contact)", "data": {{ ... }}}},
  {{"sectionTitle": "Profile Screen 2 (Traveller 1)", "data": {{ ... }}}},
  {{"sectionTitle": "Profile Screen 3 (Traveller 2)", "data": {{ ... }}}},
  {{"sectionTitle": "Profile Screen 4 (Preferences)", "data": "plain text string"}}
]
Return ONLY the JSON array. No prose, no markdown fences.\
"""


async def run(markdown: str, routing: dict) -> list[dict]:
    """Extract new traveller profile sections from document."""
    user_content = (
        f"PROFILE DOCUMENT:\n{markdown}\n\n"
        "Extract all traveller profile data and return the JSON array of section objects."
    )

    return await call_claude(_SYSTEM_PROMPT, user_content, max_tokens=3000)
