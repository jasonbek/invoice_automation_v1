"""
Agent 1: Markdown Specialist

Converts one or more invoice files into two artefacts:

  1. extract       — compact LABEL: value data block (Haiku pass). Used by the
                     routing agent and by every extractor that only needs the
                     structured field set (flights, rail, hotel, insurance, …).
  2. source_blocks — the raw Anthropic content blocks (PDF documents + mammoth
                     Markdown + email body text) that were fed to Haiku. These
                     are preserved so narrative-heavy extractors (tour, cruise)
                     can re-read the untouched document with Sonnet to build
                     the day-by-day "Itinerary at a glance" block.

Supports: PDF, .eml (email), .docx (Word), plain text / .md files.

.docx files are converted to Markdown server-side via `mammoth`, which maps
Word heading styles (Heading 1/2/3) to Markdown #/##/### — preserving the
document hierarchy that downstream itinerary extractors rely on.

.eml files are parsed with Python's built-in email module — the email body text
and any embedded PDF attachments are extracted separately before sending to Claude.
This prevents MIME-encoded base64 attachment data from being sent as raw text,
which would consume 50,000+ tokens unnecessarily.
"""

import asyncio
import base64
import email as email_lib
import email.policy
import anthropic

SYSTEM_PROMPT = """\
You are a data extraction specialist for travel agency invoices.
Your job is to extract ONLY the raw data fields from the invoice. Be extremely concise.

OUTPUT RULES:
- Output ONLY data lines in the format: LABEL: value
- NO prose, NO section headers, NO descriptions, NO marketing text, NO legal text
- NO explanations, NO formatting, NO markdown headers (##)
- If a field has multiple values (e.g. multiple passengers), list one per line
- Preserve all values EXACTLY as printed: dates, amounts, codes, names
- ALWAYS use the label "Passenger:" for every traveller/guest/client/pax name,
  regardless of what the invoice calls them — one line per person

EXTRACT THESE FIELDS (when present):
Vendor/supplier name, confirmation number, PNR/record locator, trip ref,
ticket numbers, reservation date, booking date,
passenger names, fare class RBD codes (Y M B E K L T etc), meal codes,
flight numbers, departure/arrival cities and IATA codes, departure/arrival dates and times,
seat assignments, base fare, taxes, total, commission, currency,
tour name, tour code, ACTOT code, start date, end date, duration,
hotel name, check-in, check-out, room type, cabin number, ship name,
policy number, premium, coverage dates, final payment due date,
address, phone, email, date of birth, citizenship\
"""


def _parse_eml(raw_bytes: bytes) -> tuple[str, list[dict]]:
    """Parse a .eml file into body text and a list of PDF attachment dicts.

    Strips MIME headers, encoding noise, and base64 attachment data —
    only the human-readable body and proper PDF blobs are returned.
    """
    msg = email_lib.message_from_bytes(raw_bytes, policy=email_lib.policy.default)
    body_parts: list[str] = []
    pdf_attachments: list[dict] = []

    for part in msg.walk():
        ct = part.get_content_type()
        disposition = str(part.get_content_disposition() or "")

        if ct == "text/plain" and "attachment" not in disposition:
            try:
                body_parts.append(part.get_content())
            except Exception:
                pass
        elif ct == "text/html" and not body_parts and "attachment" not in disposition:
            # Use HTML body only if no plain text was found
            try:
                body_parts.append(part.get_content())
            except Exception:
                pass
        elif ct == "application/pdf" or (
            "attachment" in disposition
            and "pdf" in (part.get_filename() or "").lower()
        ):
            payload = part.get_payload(decode=True)
            if payload:
                pdf_attachments.append(
                    {
                        "filename": part.get_filename() or "attachment.pdf",
                        "content_type": "application/pdf",
                        "content_b64": base64.b64encode(payload).decode(),
                    }
                )

    return "\n".join(body_parts), pdf_attachments


def build_source_blocks(files_b64: list[dict]) -> list[dict]:
    """Build the Anthropic content-block list from uploaded files.

    The same list is sent to Haiku (for the compact extract) and reused
    downstream by tour/cruise extractors when they need the full document
    narrative. Does NOT include any trailing instruction text — callers
    append their own instruction block.
    """
    blocks: list[dict] = []

    for f in files_b64:
        ct = f.get("content_type", "application/octet-stream")
        filename = f.get("filename", "")

        if "pdf" in ct.lower():
            blocks.append(
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": f["content_b64"],
                    },
                    "title": filename or "invoice.pdf",
                }
            )

        elif "rfc822" in ct.lower() or filename.lower().endswith(".eml"):
            raw_bytes = base64.b64decode(f["content_b64"])
            body_text, pdf_attachments = _parse_eml(raw_bytes)

            if body_text.strip():
                blocks.append({"type": "text", "text": body_text})

            for pdf in pdf_attachments:
                blocks.append(
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf["content_b64"],
                        },
                        "title": pdf["filename"],
                    }
                )

        elif (
            "wordprocessingml" in ct.lower()
            or filename.lower().endswith(".docx")
        ):
            import io
            import mammoth
            raw_bytes = base64.b64decode(f["content_b64"])
            result = mammoth.convert_to_markdown(io.BytesIO(raw_bytes))
            blocks.append({"type": "text", "text": result.value})

        else:
            raw_bytes = base64.b64decode(f["content_b64"])
            text = raw_bytes.decode("utf-8", errors="replace")
            blocks.append({"type": "text", "text": text})

    return blocks


async def run(files_b64: list[dict]) -> dict:
    """Convert one or more invoice files to a compact data extract + raw source.

    Args:
        files_b64: List of dicts with keys: filename, content_type, content_b64

    Returns:
        {
          "extract":       compact LABEL: value string (Haiku-filtered),
          "source_blocks": list of Anthropic content blocks — raw PDFs + mammoth
                           Markdown + email body text, ready to reuse downstream.
        }
    """
    client = anthropic.AsyncAnthropic(max_retries=6)

    source_blocks = build_source_blocks(files_b64)

    # Haiku call gets the source blocks + a trailing instruction block.
    haiku_content = list(source_blocks) + [
        {
            "type": "text",
            "text": (
                "Extract all invoice data fields from the attached document(s). "
                "Output only LABEL: value lines. No prose, no headers, no extra text."
            ),
        }
    ]

    app_retries = 8
    for attempt in range(app_retries + 1):
        try:
            message = await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=8192,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": haiku_content}],
            )
            break
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as e:
            if attempt < app_retries:
                delay = min(30 * (2 ** attempt), 300)
                print(f"[markdown_agent] {type(e).__name__}, retrying in {delay}s "
                      f"(attempt {attempt + 1}/{app_retries})")
                await asyncio.sleep(delay)
                continue
            raise
        except anthropic.APIStatusError as e:
            if e.status_code == 529 and attempt < app_retries:
                delay = min(30 * (2 ** attempt), 300)
                print(f"[markdown_agent] Anthropic overloaded (529), retrying in {delay}s "
                      f"(attempt {attempt + 1}/{app_retries})")
                await asyncio.sleep(delay)
                continue
            raise

    return {
        "extract": message.content[0].text,
        "source_blocks": source_blocks,
    }
