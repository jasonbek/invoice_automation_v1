"""
Agent 1: Markdown Specialist

Converts one or more invoice files into a compact, data-only text extract.
Supports: PDF, .eml (email), plain text / .md files.

.eml files are parsed with Python's built-in email module — the email body text
and any embedded PDF attachments are extracted separately before sending to Claude.
This prevents MIME-encoded base64 attachment data from being sent as raw text,
which would consume 50,000+ tokens unnecessarily.
"""

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


async def run(files_b64: list[dict]) -> str:
    """Convert one or more invoice files to a compact data extract.

    Args:
        files_b64: List of dicts with keys: filename, content_type, content_b64

    Returns:
        Compact LABEL: value string containing all extracted invoice fields.
    """
    client = anthropic.AsyncAnthropic(max_retries=6)

    content = []

    for f in files_b64:
        ct = f.get("content_type", "application/octet-stream")
        filename = f.get("filename", "")

        if "pdf" in ct.lower():
            # Native PDF — Claude reads it directly
            content.append(
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
            # Email file — parse MIME structure to extract body + PDF attachments
            raw_bytes = base64.b64decode(f["content_b64"])
            body_text, pdf_attachments = _parse_eml(raw_bytes)

            if body_text.strip():
                content.append({"type": "text", "text": body_text})

            for pdf in pdf_attachments:
                content.append(
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

        else:
            # Plain text or .md file
            raw_bytes = base64.b64decode(f["content_b64"])
            text = raw_bytes.decode("utf-8", errors="replace")
            content.append({"type": "text", "text": text})

    content.append(
        {
            "type": "text",
            "text": (
                "Extract all invoice data fields from the attached document(s). "
                "Output only LABEL: value lines. No prose, no headers, no extra text."
            ),
        }
    )

    message = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
    )

    return message.content[0].text
