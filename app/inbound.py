"""
Inbound email intake via Resend Receiving.

Lets a supplier invoice be processed by forwarding it to a Resend receiving
address instead of using the /form upload — same pipeline either way. Resend
sends a webhook with metadata only; this module verifies the webhook
signature and fetches the actual body/attachment content via the Resend
Receiving API.

Uses httpx (already a project dependency) — no Resend SDK needed, consistent
with app/email_sender.py.
"""

import base64
import hashlib
import hmac
import time

import httpx

RECEIVING_URL = "https://api.resend.com/emails/receiving"


# ── Webhook signature verification (Svix-based, same scheme Resend uses) ───────

def verify_resend_signature(
    secret: str,
    payload: bytes,
    svix_id: str,
    svix_timestamp: str,
    svix_signature: str,
    tolerance_seconds: int = 300,
) -> bool:
    """Verify a Resend webhook's svix-id/svix-timestamp/svix-signature headers.

    Signed content is "{svix_id}.{svix_timestamp}.{raw_body}", HMAC-SHA256
    keyed by the base64 portion of the "whsec_..." signing secret.
    svix-signature may list multiple space-separated "v1,<base64sig>"
    candidates (for key rotation) — any match is accepted. The raw request
    body must be used here, not a re-serialized/parsed copy, or the
    signature will never match.
    """
    if not (svix_id and svix_timestamp and svix_signature):
        return False

    try:
        timestamp = int(svix_timestamp)
    except ValueError:
        return False
    if abs(time.time() - timestamp) > tolerance_seconds:
        return False

    secret_bytes = base64.b64decode(secret.removeprefix("whsec_"))
    signed_content = f"{svix_id}.{svix_timestamp}.".encode() + payload
    expected = base64.b64encode(
        hmac.new(secret_bytes, signed_content, hashlib.sha256).digest()
    ).decode()

    for candidate in svix_signature.split():
        _, _, sig = candidate.partition(",")
        if hmac.compare_digest(sig, expected):
            return True
    return False


# ── Resend Receiving API ────────────────────────────────────────────────────────

async def fetch_received_email(email_id: str, api_key: str) -> dict:
    """GET the full body (text/html) of a received email."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{RECEIVING_URL}/{email_id}",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        resp.raise_for_status()
        return resp.json()["data"]


async def fetch_attachment_list(email_id: str, api_key: str) -> list[dict]:
    """GET the list of attachments (with download_url) for a received email."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{RECEIVING_URL}/{email_id}/attachments",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        resp.raise_for_status()
        return resp.json()["data"]


async def download_attachment(download_url: str) -> bytes:
    """Download an attachment's raw bytes from its (pre-signed) download_url."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(download_url)
        resp.raise_for_status()
        return resp.content


# ── Orchestration ───────────────────────────────────────────────────────────────

async def build_files_b64(
    email_id: str,
    webhook_attachments: list[dict],
    api_key: str,
) -> list[dict]:
    """Build the files_b64 list run_pipeline expects from a received email.

    Matches the {"filename", "content_type", "content_b64"} shape _expand_upload()
    already produces in app/main.py, so every downstream format handler (PDF,
    .docx, .eml, .md, .txt, .zip) keeps working unchanged. If the email has no
    attachments, its body text is wrapped as a single synthetic .txt file so
    invoices that only exist in the email body still flow through the same
    pipeline.
    """
    if not webhook_attachments:
        email = await fetch_received_email(email_id, api_key)
        body_text = email.get("text") or email.get("html") or ""
        return [{
            "filename": "email-body.txt",
            "content_type": "text/plain",
            "content_b64": base64.b64encode(body_text.encode()).decode(),
        }]

    content_type_by_id = {
        a.get("id"): a.get("content_type", "application/octet-stream")
        for a in webhook_attachments
    }
    attachment_list = await fetch_attachment_list(email_id, api_key)

    files_b64 = []
    for att in attachment_list:
        raw = await download_attachment(att["download_url"])
        files_b64.append({
            "filename": att.get("filename", "attachment"),
            "content_type": content_type_by_id.get(att.get("id"), "application/octet-stream"),
            "content_b64": base64.b64encode(raw).decode(),
        })
    return files_b64
