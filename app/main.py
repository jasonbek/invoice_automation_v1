"""
Invoice Automation Pipeline — Modal.com Entry Point

Flow:
  POST /process-invoice  →  spawn run_pipeline (background)  →  POST results to callback_url

n8n sends files + metadata here; we return 202 immediately and do all work async.
"""

import base64

import modal
from fastapi import FastAPI, File, Form, Request, UploadFile

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

# ── FastAPI app (runs inside Modal ASGI container) ─────────────────────────────

web_app = FastAPI(title="Invoice Processor", version="1.0.0")


@web_app.get("/health")
async def health():
    return {"status": "ok"}


@web_app.post("/process-invoice", status_code=202)
async def receive_invoice(
    vendor: str = Form(...),
    callback_url: str = Form(default="https://beks.app.n8n.cloud/webhook-test/invoice_results"),
    service_fee: float = Form(0.0),
    booking_type_hint: str = Form(""),
    files: list[UploadFile] = File(...),
):
    """
    Accept invoice files and form metadata.
    Returns 202 immediately; processing happens in a background Modal function.

    Form fields:
      vendor            – supplier name (hint; router will normalize)
      callback_url      – n8n Webhook Trigger URL to POST results back to
      service_fee       – agency service fee amount (0 if none)
      booking_type_hint – optional hint (flight / tour / hotel / etc.)
      files             – one or more PDF or .md invoice attachments
    """
    # Read all files into memory and base64-encode for safe serialization
    files_b64 = []
    for f in files:
        raw = await f.read()
        files_b64.append(
            {
                "filename": f.filename or "attachment",
                "content_type": f.content_type or "application/octet-stream",
                "content_b64": base64.b64encode(raw).decode(),
            }
        )

    # Spawn background pipeline — non-blocking, Modal handles scheduling
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
    Used by n8n to avoid multipart/form-data binary field limitations.

    JSON body:
      {
        "vendor": "...",
        "callback_url": "...",
        "service_fee": 0.0,
        "booking_type_hint": "",
        "files": [
          {"filename": "...", "content_type": "...", "content_b64": "..."},
          ...
        ]
      }

    n8n stores binary data internally as base64, so content_b64 can be
    passed directly from item.binary[key].data without any conversion.
    """
    body = await request.json()

    run_pipeline.spawn(
        vendor=body.get("vendor", ""),
        callback_url=body.get("callback_url", "https://beks.app.n8n.cloud/webhook-test/invoice_results"),
        service_fee=float(body.get("service_fee", 0.0)),
        booking_type_hint=body.get("booking_type_hint", ""),
        files_b64=body.get("files", []),
    )

    return {"status": "accepted", "files_received": len(body.get("files", []))}


# ── Background pipeline function ───────────────────────────────────────────────

@app.function(image=image, secrets=[ANTHROPIC_SECRET], timeout=300)
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
      4. HTTP POST results    — deliver to n8n callback_url
    """
    import asyncio
    import httpx

    # Lazy imports — these run inside the Modal container where app/ is available
    from app.agents.markdown_agent import run as markdown_run
    from app.agents.routing_agent import run as routing_run
    from app.agents.extractors import run_all

    try:
        # Step 1: Convert all files to structured Markdown
        markdown = await markdown_run(files_b64)

        # Agents 1 & 2 use haiku; Agent 3+ use sonnet — separate rate limit pools.
        # Small pause still helps avoid bursting the sonnet extractor quota.
        await asyncio.sleep(5)

        # Step 2: Classify vendor and detect booking type(s)
        routing = await routing_run(markdown, vendor, booking_type_hint)

        await asyncio.sleep(5)

        # Step 3: Run all required extractors in parallel
        sections = await run_all(markdown, routing, service_fee)

        payload = {"status": "success", "sections": sections}

    except Exception as exc:
        payload = {
            "status": "error",
            "error": str(exc),
            "sections": [],
        }

    # Deliver results back to n8n
    async with httpx.AsyncClient(timeout=30.0) as client:
        await client.post(callback_url, json=payload)


# ── Modal ASGI entrypoint ──────────────────────────────────────────────────────

@app.function(image=image, secrets=[ANTHROPIC_SECRET])
@modal.asgi_app()
def fastapi_entrypoint():
    return web_app
