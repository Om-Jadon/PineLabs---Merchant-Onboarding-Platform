import asyncio
import hmac
import json
from contextlib import asynccontextmanager, suppress
from datetime import timedelta

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from .contracts import IssueInput, VerifyInput
from .db import Delivery, Merchant, Session, uid
from .evidence import normalize
from .settings import CALLBACK_URL, CHECK_TIMEOUT, LEGAL_NAME, NEW_GST, OLD_GST, PAN, now, secret, sign


def internal(key: str = Header("", alias="X-Simulator-Key")):
    if not hmac.compare_digest(key, secret("simulator").hex()):
        raise HTTPException(403, "Simulator credentials required.")


def deliver_one():
    with Session.begin() as db:
        row = db.scalar(
            select(Delivery)
            .where(~Delivery.delivered, Delivery.due <= now())
            .order_by(Delivery.due)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if row is None:
            return False
        row.tries += 1
        row.due = now() + timedelta(seconds=min(30, 2 ** min(row.tries, 5)))
        attempt_id, provider, payload = row.attempt_id, row.provider, row.payload
    content = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    try:
        response = httpx.post(
            f"{CALLBACK_URL}/api/webhooks/{provider.lower()}",
            content=content,
            timeout=4,
            headers={"content-type": "application/json", "X-Webhook-Signature": sign(provider, content)},
        )
        response.raise_for_status()
    except httpx.HTTPError:
        return True
    with Session.begin() as db:
        row = db.get(Delivery, attempt_id, with_for_update=True)
        row.delivered = True
    return True


async def deliver_loop():
    while True:
        try:
            ran = await asyncio.to_thread(deliver_one)
        except Exception as error:
            print("Simulator delivery loop:", type(error).__name__, flush=True)
            ran = False
        await asyncio.sleep(0.12 if ran else 0.35)


@asynccontextmanager
async def lifespan(_):
    task = asyncio.create_task(deliver_loop())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="Local provider simulator", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/{provider}/verifications", status_code=202, dependencies=[Depends(internal)])
def verify(provider: str, body: VerifyInput):
    kind = provider.upper()
    if kind not in {"PAN", "GST"}:
        raise HTTPException(404)
    identity = normalize(body.legal_name) == normalize(LEGAL_NAME) and body.pan == PAN
    reason = "RECORD_MISMATCH"
    if identity:
        reason = (
            "PAN_MATCH"
            if kind == "PAN"
            else {OLD_GST: "GST_INACTIVE", NEW_GST: "GST_ACTIVE"}.get(body.gstin, reason)
        )
    payload = {
        "event_id": uid(),
        "attempt_id": body.attempt_id,
        "provider_ref": f"{kind}-{body.attempt_id}",
        "outcome": "PASSED" if reason in {"PAN_MATCH", "GST_ACTIVE"} else "FAILED",
        "reason": reason,
    }
    delay = 1 if kind == "PAN" else 3
    if body.scenario == "late_callback" and kind == "GST" and body.number == 1:
        delay = CHECK_TIMEOUT + 2
    with Session.begin() as db:
        db.execute(
            insert(Delivery)
            .values(
                attempt_id=body.attempt_id,
                provider=kind,
                payload=payload,
                due=now() + timedelta(seconds=delay),
            )
            .on_conflict_do_nothing(index_elements=["attempt_id"])
        )
        row = db.get(Delivery, body.attempt_id)
        if row.provider != kind:
            raise HTTPException(409, "Attempt belongs to another provider.")
        return {"provider_ref": row.payload["provider_ref"]}


@app.post("/merchants", dependencies=[Depends(internal)])
def issue(body: IssueInput):
    with Session.begin() as db:
        created = db.scalar(
            insert(Merchant)
            .values(case_id=body.case_id, mid="MID-" + uid().replace("-", "")[:16].upper())
            .on_conflict_do_nothing(index_elements=["case_id"])
            .returning(Merchant.mid)
        )
        mid = created or db.get(Merchant, body.case_id).mid
    if created and body.scenario == "lost_mid_response":
        raise HTTPException(503, "Simulated lost issuance response.")
    return {"mid": mid}


@app.post("/replay/{attempt_id}", status_code=202, dependencies=[Depends(internal)])
def replay(attempt_id: str):
    with Session.begin() as db:
        row = db.get(Delivery, attempt_id, with_for_update=True)
        if not row:
            raise HTTPException(404)
        row.delivered, row.due = False, now()
    return {"queued": True}
