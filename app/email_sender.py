"""
Email delivery via Resend API.

send_results() — formats the extracted invoice sections as an HTML email
                 and POSTs to https://api.resend.com/emails

Uses httpx (already a project dependency) — no Resend SDK needed.
Reads RESEND_API_KEY, FROM_EMAIL, TO_EMAIL from environment (Modal secrets).
"""

import html
import os
from datetime import datetime

import httpx

RESEND_URL = "https://api.resend.com/emails"


# ── HTML helpers ───────────────────────────────────────────────────────────────

_TD_LABEL = (
    'style="padding:6px 10px;font-weight:600;color:#374151;background:#f9fafb;'
    'border:1px solid #e5e7eb;width:35%;vertical-align:top"'
)
_TD_VALUE = (
    'style="padding:6px 10px;color:#111827;border:1px solid #e5e7eb;'
    'font-family:monospace;font-size:0.9em;white-space:pre-wrap;word-break:break-word"'
)
_TABLE_STYLE = 'style="border-collapse:collapse;width:100%;margin-bottom:8px"'


def _kv_table(d: dict) -> str:
    """Render a dict as a two-column key/value HTML table."""
    rows = "".join(
        f"<tr><td {_TD_LABEL}>{html.escape(str(k))}</td>"
        f"<td {_TD_VALUE}>{html.escape(str(v))}</td></tr>"
        for k, v in d.items()
    )
    return f"<table {_TABLE_STYLE}>{rows}</table>"


def _section_html(section: dict) -> str:
    """Render one section (title + data) as an HTML block."""
    title = html.escape(section.get("sectionTitle", "Section"))
    data = section.get("data")

    if isinstance(data, dict):
        content = _kv_table(data)

    elif isinstance(data, list):
        parts = []
        for i, item in enumerate(data, 1):
            if isinstance(item, dict):
                label = (
                    f'<p style="margin:8px 0 2px;font-size:0.8em;color:#6b7280">'
                    f"#{i}</p>"
                )
                parts.append(label + _kv_table(item))
            else:
                parts.append(f"<p>{html.escape(str(item))}</p>")
        content = "".join(parts) or "<em>Empty</em>"

    elif isinstance(data, str):
        content = (
            f'<pre style="background:#f9fafb;padding:12px;border-radius:4px;'
            f'font-size:0.85em;white-space:pre-wrap;border:1px solid #e5e7eb">'
            f"{html.escape(data)}</pre>"
        )

    else:
        content = f"<p>{html.escape(str(data))}</p>"

    return (
        f'<div style="margin-bottom:28px">'
        f'<h3 style="margin:0 0 8px;font-size:1rem;color:#1d4ed8;'
        f'border-bottom:2px solid #dbeafe;padding-bottom:6px">{title}</h3>'
        f"{content}"
        f"</div>"
    )


def _build_html(
    traveller_name: str,
    vendor: str,
    booking_types: list[str],
    sections: list[dict],
    status: str,
    error: str | None,
) -> str:
    """Build the full HTML email body."""
    types_str = " + ".join(t.replace("_", " ").title() for t in booking_types) if booking_types else "Unknown"
    timestamp = datetime.now().strftime("%b %d, %Y at %I:%M %p")

    if status == "error":
        body = (
            f'<div style="background:#fef2f2;border:1px solid #fecaca;border-radius:6px;padding:16px;margin-bottom:24px">'
            f'<strong style="color:#dc2626">Processing Error</strong>'
            f'<pre style="margin:8px 0 0;white-space:pre-wrap;color:#7f1d1d">{html.escape(str(error))}</pre>'
            f"</div>"
        )
    else:
        body = "".join(_section_html(s) for s in sections)

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:system-ui,sans-serif;max-width:700px;margin:0 auto;padding:24px;color:#111827">
  <div style="border-bottom:3px solid #2563eb;padding-bottom:16px;margin-bottom:24px">
    <h1 style="margin:0;font-size:1.4rem;color:#1e3a8a">{html.escape(traveller_name)}</h1>
    <p style="margin:4px 0 0;color:#6b7280;font-size:0.9em">
      {html.escape(vendor)} &nbsp;·&nbsp; {html.escape(types_str)} &nbsp;·&nbsp; {timestamp}
    </p>
  </div>
  {body}
  <p style="margin-top:32px;font-size:0.75em;color:#9ca3af;border-top:1px solid #e5e7eb;padding-top:12px">
    Sent by Invoice Automation Pipeline
  </p>
</body>
</html>"""


# ── Public API ─────────────────────────────────────────────────────────────────

async def send_results(
    traveller_name: str,
    vendor: str,
    booking_types: list[str],
    sections: list[dict],
    status: str,
    error: str | None = None,
) -> None:
    """Send processed invoice results as an HTML email via Resend.

    Args:
        traveller_name: Extracted from sections (e.g. "John Smith").
        vendor:         Normalized vendor name from routing.
        booking_types:  List of booking types (e.g. ["flight", "tour"]).
        sections:       Flat list of section dicts from run_all().
        status:         "success" or "error".
        error:          Error message string if status == "error".

    Raises:
        httpx.HTTPStatusError: If Resend returns a non-2xx response.
    """
    api_key = os.environ["RESEND_API_KEY"]
    from_email = os.environ["FROM_EMAIL"]
    to_email = os.environ["TO_EMAIL"]

    types_str = " + ".join(t.replace("_", " ").title() for t in booking_types) if booking_types else "Unknown"
    subject = f"Invoice: {traveller_name} — {vendor} ({types_str})"

    email_html = _build_html(
        traveller_name=traveller_name,
        vendor=vendor,
        booking_types=booking_types,
        sections=sections,
        status=status,
        error=error,
    )

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            RESEND_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "from": from_email,
                "to": [to_email],
                "subject": subject,
                "html": email_html,
            },
        )
        resp.raise_for_status()
