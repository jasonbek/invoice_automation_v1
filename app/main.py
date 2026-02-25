"""
Invoice Automation Pipeline — Modal.com Entry Point

Flow:
  GET  /form             →  HTML upload form (browser)
  POST /process-invoice  →  spawn run_pipeline (background)  →  email results

Processing is async: the endpoint returns 202 immediately; the pipeline runs in
a Modal background function and emails results when done.

n8n backward-compat: if callback_url is supplied (via /process-invoice-json),
the pipeline also POSTs the JSON payload to that URL.
"""

import base64
import io
import zipfile

import modal
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse

# ── Modal app setup ────────────────────────────────────────────────────────────

app = modal.App("invoice-processor")

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "fastapi>=0.104.0",
        "anthropic>=0.40.0",
        "httpx>=0.27.0",
        "python-multipart>=0.0.9",
    )
    .add_local_python_source("app")
)

ANTHROPIC_SECRET = modal.Secret.from_name("anthropic")
RESEND_SECRET = modal.Secret.from_name("resend")

# ── Web form HTML ──────────────────────────────────────────────────────────────

_FORM_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Invoice Processor</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; }
    body {
      font-family: system-ui, -apple-system, sans-serif;
      background: #f1f5f9;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      margin: 0;
      padding: 24px;
    }
    .card {
      background: white;
      border-radius: 12px;
      box-shadow: 0 4px 24px rgba(0,0,0,0.08);
      padding: 40px;
      width: 100%;
      max-width: 520px;
    }
    h1 { margin: 0 0 6px; font-size: 1.5rem; color: #1e3a8a; }
    .subtitle { margin: 0 0 28px; color: #64748b; font-size: 0.9rem; }
    label {
      display: block;
      font-weight: 600;
      font-size: 0.875rem;
      color: #374151;
      margin-bottom: 4px;
    }
    .field { margin-bottom: 20px; }
    input[type="text"],
    input[type="number"],
    select {
      width: 100%;
      padding: 9px 12px;
      border: 1px solid #d1d5db;
      border-radius: 6px;
      font-size: 0.95rem;
      color: #111827;
      outline: none;
      transition: border-color 0.15s;
    }
    input[type="text"]:focus,
    input[type="number"]:focus,
    select:focus {
      border-color: #2563eb;
      box-shadow: 0 0 0 3px rgba(37,99,235,0.1);
    }
    input[type="file"] {
      width: 100%;
      padding: 8px 0;
      font-size: 0.9rem;
      color: #374151;
    }
    .hint { font-size: 0.78rem; color: #6b7280; margin-top: 4px; }
    button {
      width: 100%;
      padding: 11px;
      background: #2563eb;
      color: white;
      border: none;
      border-radius: 6px;
      font-size: 1rem;
      font-weight: 600;
      cursor: pointer;
      margin-top: 8px;
      transition: background 0.15s;
    }
    button:hover { background: #1d4ed8; }
    button:active { background: #1e40af; }
    button:disabled { background: #93c5fd; cursor: not-allowed; }
    .submitted {
      display: none;
      text-align: center;
      padding: 24px 0 8px;
    }
    .submitted .check {
      font-size: 2.5rem;
      margin-bottom: 8px;
    }
    .submitted p {
      margin: 0;
      color: #374151;
      font-size: 0.95rem;
    }
    .submitted .sub {
      color: #6b7280;
      font-size: 0.82rem;
      margin-top: 6px !important;
    }
  </style>
</head>
<body>
  <div class="card">
    <h1>Invoice Processor</h1>
    <p class="subtitle">Upload a supplier invoice to extract and email the data.</p>

    <form id="invoiceForm" action="/process-invoice" method="post" enctype="multipart/form-data">

      <div class="field">
        <label for="vendor">Vendor</label>
        <input type="text" id="vendor" name="vendor" required
               placeholder="e.g. Air Canada Internet, WestJet, ADX">
      </div>

      <div class="field">
        <label for="booking_type_hint">Booking Type</label>
        <select id="booking_type_hint" name="booking_type_hint">
          <option value="">Auto-detect (recommended)</option>
          <option value="flight">Flight</option>
          <option value="rail">Rail / Train</option>
          <option value="tour">Tour / Land Package</option>
          <option value="day_tour">Day Tour (Viator)</option>
          <option value="hotel">Hotel</option>
          <option value="cruise">Cruise</option>
          <option value="insurance">Insurance</option>
          <option value="new_traveller">New Traveller Profile</option>
        </select>
        <p class="hint">Leave on Auto-detect if unsure — the AI will figure it out.</p>
      </div>

      <div class="field">
        <label for="service_fee">Service Fee ($)</label>
        <input type="number" id="service_fee" name="service_fee"
               value="0" min="0" step="0.01">
        <p class="hint">Enter 0 if no agency service fee applies.</p>
      </div>

      <div class="field">
        <label for="files">Invoice Files</label>
        <input type="file" id="files" name="files" multiple
               accept=".pdf,.eml,.md,.zip">
        <p class="hint">Accepts PDF, .eml, .md, or .zip (zip is unpacked automatically).
          Multiple files allowed.</p>
      </div>

      <button type="submit" id="submitBtn">Process Invoice</button>

    </form>

    <div class="submitted" id="successMsg">
      <div class="check">&#10003;</div>
      <p>Invoice submitted successfully.</p>
      <p class="sub">Results will arrive by email in about 30–60 seconds.</p>
      <p class="sub" style="margin-top:16px !important">
        <a href="/form" style="color:#2563eb">Submit another invoice</a>
      </p>
    </div>
  </div>

  <script>
    document.getElementById('invoiceForm').addEventListener('submit', function(e) {
      var btn = document.getElementById('submitBtn');
      var msg = document.getElementById('successMsg');
      btn.disabled = true;
      btn.textContent = 'Submitting\u2026';
      // Show confirmation after a short delay to allow the form POST to fire
      setTimeout(function() {
        document.getElementById('invoiceForm').style.display = 'none';
        msg.style.display = 'block';
      }, 800);
    });
  </script>
</body>
</html>
"""

# ── Zip expansion helper ───────────────────────────────────────────────────────

def _expand_upload(filename: str, content_type: str, raw: bytes) -> list[dict]:
    """Return a list of file dicts for the pipeline.

    Zip files are transparently unpacked — each inner PDF, .eml, or .md is
    returned as its own entry. Non-zip files are returned as-is in a 1-item list.
    macOS metadata entries (__MACOSX/, .DS_Store) are silently skipped.
    """
    is_zip = filename.lower().endswith(".zip") or "zip" in content_type.lower()
    if is_zip:
        results = []
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                for member in zf.namelist():
                    # Skip directories, macOS metadata, and hidden files
                    if member.endswith("/") or "__MACOSX" in member or member.split("/")[-1].startswith("."):
                        continue
                    lower = member.lower()
                    if lower.endswith(".pdf"):
                        ct = "application/pdf"
                    elif lower.endswith(".eml"):
                        ct = "message/rfc822"
                    elif lower.endswith(".md"):
                        ct = "text/markdown"
                    else:
                        continue  # ignore non-invoice files inside the zip
                    member_bytes = zf.read(member)
                    basename = member.split("/")[-1]
                    results.append({
                        "filename": basename,
                        "content_type": ct,
                        "content_b64": base64.b64encode(member_bytes).decode(),
                    })
        except zipfile.BadZipFile:
            pass  # fall through — treat as a regular file
        if results:
            return results

    # Non-zip or unreadable zip — return as-is
    return [{
        "filename": filename,
        "content_type": content_type,
        "content_b64": base64.b64encode(raw).decode(),
    }]


# ── FastAPI app (runs inside Modal ASGI container) ─────────────────────────────

web_app = FastAPI(title="Invoice Processor", version="2.0.0")


@web_app.get("/health")
async def health():
    return {"status": "ok"}


@web_app.get("/form", response_class=HTMLResponse)
async def form():
    """Serve the invoice upload form."""
    return _FORM_HTML


@web_app.post("/process-invoice", status_code=202)
async def receive_invoice(
    vendor: str = Form(...),
    callback_url: str = Form(""),
    service_fee: float = Form(0.0),
    booking_type_hint: str = Form(""),
    files: list[UploadFile] = File(...),
):
    """
    Accept invoice files and form metadata.
    Returns 202 immediately; processing and email delivery happen in the background.

    Form fields:
      vendor            – supplier name (hint; router will normalize)
      callback_url      – optional: if set, results are also POSTed here (n8n compat)
      service_fee       – agency service fee amount (0 if none)
      booking_type_hint – optional hint (flight / tour / hotel / etc.)
      files             – one or more PDF, .eml, or .md invoice attachments
    """
    files_b64 = []
    for f in files:
        raw = await f.read()
        files_b64.extend(
            _expand_upload(
                f.filename or "attachment",
                f.content_type or "application/octet-stream",
                raw,
            )
        )

    run_pipeline.spawn(
        vendor=vendor,
        callback_url=callback_url,
        service_fee=service_fee,
        booking_type_hint=booking_type_hint,
        files_b64=files_b64,
    )

    return {"status": "accepted", "files_received": len(files_b64)}


@web_app.post("/process-invoice-json", status_code=202)
async def receive_invoice_json(request: Request):
    """
    Accept invoice data as JSON with base64-encoded files.
    Kept for backward compatibility with n8n and direct API calls.

    JSON body:
      {
        "vendor": "...",
        "callback_url": "",          (optional)
        "service_fee": 0.0,
        "booking_type_hint": "",
        "files": [
          {"filename": "...", "content_type": "...", "content_b64": "..."},
          ...
        ]
      }
    """
    body = await request.json()

    run_pipeline.spawn(
        vendor=body.get("vendor", ""),
        callback_url=body.get("callback_url", ""),
        service_fee=float(body.get("service_fee", 0.0)),
        booking_type_hint=body.get("booking_type_hint", ""),
        files_b64=body.get("files", []),
    )

    return {"status": "accepted", "files_received": len(body.get("files", []))}


# ── Traveller name extraction ──────────────────────────────────────────────────

def _extract_traveller_name(sections: list[dict], markdown: str = "") -> str:
    """Extract the first usable traveller name for the email subject line.

    Search order:
      1. Agent 1 markdown  — regex scan for Passenger/Traveller/Guest lines
      2. Flight Passengers section (array) → first item's passengerName
      3. Profile Contact section (dict)    → firstName + lastName
      4. Any dict section with a passengerName key
    Falls back to "Unknown" if nothing found.

    The name is ONLY used for the email subject — it is never injected into
    the CBO section schemas, so UI.Vision macro output is unaffected.
    """
    import re

    # 1. Parse markdown from Agent 1 — most reliable source across all booking types.
    #    Matches lines like: "Passenger: John Smith", "Passengers: John Smith, Jane Smith",
    #    "Traveller: ...", "Guest: ..." produced by the markdown agent.
    if markdown:
        match = re.search(
            r"(?im)^Passenger:\s*(.+)$",
            markdown,
        )
        if match:
            first = match.group(1).strip()
            if first:
                return first

    # 2. Flight: Section 3 (Passengers) data is an array of passenger objects
    for section in sections:
        title = section.get("sectionTitle", "")
        data = section.get("data")

        if "Passengers" in title and isinstance(data, list) and data:
            name = data[0].get("passengerName", "")
            if name:
                return name

    # 3. New Traveller Profile: Section 1 (Contact) has firstName + lastName
    for section in sections:
        title = section.get("sectionTitle", "")
        data = section.get("data")

        if "Contact" in title and isinstance(data, dict):
            first = data.get("firstName", "")
            last = data.get("lastName", "")
            if first or last:
                return f"{first} {last}".strip()

    # 4. Fallback: any dict section with a passengerName key
    for section in sections:
        data = section.get("data")
        if isinstance(data, dict):
            name = data.get("passengerName", "")
            if name:
                return name

    return "Unknown"


# ── Background pipeline function ───────────────────────────────────────────────

@app.function(image=image, secrets=[ANTHROPIC_SECRET, RESEND_SECRET], timeout=300)
async def run_pipeline(
    vendor: str,
    callback_url: str,
    service_fee: float,
    booking_type_hint: str,
    files_b64: list[dict],
):
    """
    Full agent pipeline running inside a Modal worker:
      1. Markdown Specialist  — files → structured Markdown
      2. Routing Specialist   — Markdown → {vendor, ruleSet, bookingTypes[]}
      3. Schema Extractors    — parallel extraction per booking type
      4. Email results        — formatted HTML email via Resend
      5. Callback POST        — if callback_url is set (n8n backward compat)
    """
    import asyncio
    import httpx

    from app.agents.markdown_agent import run as markdown_run
    from app.agents.routing_agent import run as routing_run
    from app.agents.extractors import run_all
    from app.email_sender import send_results

    routing: dict = {}
    sections: list[dict] = []
    markdown: str = ""
    status = "success"
    error = None

    try:
        # Step 1: Convert all files to structured Markdown
        markdown = await markdown_run(files_b64)

        # Agents 1 & 2 use haiku; Agent 3+ use sonnet — separate rate limit pools.
        await asyncio.sleep(5)

        # Step 2: Classify vendor and detect booking type(s)
        routing = await routing_run(markdown, vendor, booking_type_hint)

        await asyncio.sleep(5)

        # Step 3: Run all required extractors in parallel
        sections = await run_all(markdown, routing, service_fee)

    except Exception as exc:
        status = "error"
        error = str(exc)

    # Step 4: Email results (always runs, even on error)
    traveller_name = _extract_traveller_name(sections, markdown=markdown)

    await send_results(
        traveller_name=traveller_name,
        vendor=routing.get("vendor", vendor),
        booking_types=routing.get("bookingTypes", []),
        sections=sections,
        status=status,
        error=error,
        attachments=files_b64,
    )

    # Step 5: Also POST to callback_url if one was provided (n8n / API compat)
    if callback_url:
        payload: dict = {
            "status": status,
            "traveller_name": traveller_name,
            "sections": sections,
        }
        if error:
            payload["error"] = error
        async with httpx.AsyncClient(timeout=30.0) as client:
            await client.post(callback_url, json=payload)


# ── Modal ASGI entrypoint ──────────────────────────────────────────────────────

@app.function(image=image, secrets=[ANTHROPIC_SECRET, RESEND_SECRET])
@modal.asgi_app()
def fastapi_entrypoint():
    return web_app
