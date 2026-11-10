"""Lead capture and the magic-link gate (M6).

No email provider is configured, so the token is returned in ``dev_token`` and
logged instead of sent. That is honest: the flow is testable today, and Resend
(or any sender) only has to replace ``_send_magic_link``.

``POST /api/runs`` enforces the gate server-side when ``REQUIRE_LEAD_GATE`` is on.
A form-only gate is cosmetic — anything with curl could skip it.
"""

from __future__ import annotations

import re
import secrets
import time
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter()

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
LEAD_HEADER = "x-session-token"


class LeadIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str
    role: Literal["architect", "developer", "builder", "contractor", "other"]
    company: str = ""
    country: str = "ID"
    project_type: str = ""
    phone: str = ""
    consent: bool = False


class LeadOut(BaseModel):
    lead_id: str
    status: Literal["pending_verification"] = "pending_verification"
    dev_token: str | None = None


def _send_magic_link(email: str, token: str) -> None:
    """No provider wired: log instead of faking a delivery receipt."""
    import logging

    logging.getLogger("villaai.leads").info(
        "magic link for %s: /api/leads/verify?token=%s", email, token
    )


@router.post("/leads", response_model=LeadOut)
def create_lead(body: LeadIn):
    from app.core import bootstrap

    if not _EMAIL_RE.match(body.email or ""):
        raise HTTPException(status_code=422, detail="a valid work email is required")
    if not body.consent:
        raise HTTPException(status_code=422,
                            detail="consent is required to store these details")

    store = bootstrap.get_store()
    lead_id = "lead-" + uuid.uuid4().hex[:10]
    token = secrets.token_urlsafe(16)
    record = {
        "id": lead_id, "name": body.name, "email": body.email.lower(), "role": body.role,
        "company": body.company, "country": body.country,
        "project_type": body.project_type, "phone": body.phone,
        "consent": body.consent, "status": "pending_verification",
        "token": token, "created_at": time.time(),
    }
    store.save_lead(lead_id, record)
    store.save_lead(f"token-{token}", {"lead_id": lead_id, "email": record["email"]})
    _send_magic_link(record["email"], token)
    return LeadOut(lead_id=lead_id, dev_token=token)


class VerifyIn(BaseModel):
    token: str


@router.post("/leads/verify")
def verify_lead(body: VerifyIn):
    from app.core import bootstrap

    store = bootstrap.get_store()
    rec = store.get_lead(f"token-{body.token}")
    if not rec:
        raise HTTPException(status_code=400, detail="this link is not valid or has expired")
    lead_id = rec["lead_id"] if isinstance(rec, dict) else str(rec)
    lead = store.get_lead(lead_id)
    if not lead:
        raise HTTPException(status_code=400, detail="the lead this link belongs to is gone")
    lead = dict(lead) if isinstance(lead, dict) else dict(lead.__dict__)
    lead["status"] = "verified"
    lead["verified_at"] = time.time()
    store.save_lead(lead_id, lead)
    return {"status": "verified", "session_token": lead_id, "lead_id": lead_id}


def require_lead(request: Request) -> str | None:
    """Raise 403 LEAD_GATE unless a verified session is present, when enabled."""
    from app.core.config import settings

    if not settings.REQUIRE_LEAD_GATE:
        return None
    token = request.headers.get(LEAD_HEADER)
    if not token:
        raise HTTPException(status_code=403,
                            detail={"code": "LEAD_GATE",
                                    "message": "verify your email before submitting a plan"})
    from app.core import bootstrap

    lead = bootstrap.get_store().get_lead(token)
    status = lead.get("status") if isinstance(lead, dict) else getattr(lead, "status", None)
    if status != "verified":
        raise HTTPException(status_code=403,
                            detail={"code": "LEAD_GATE",
                                    "message": "this session is not verified"})
    return token


__all__ = ["router", "require_lead", "LEAD_HEADER"]
