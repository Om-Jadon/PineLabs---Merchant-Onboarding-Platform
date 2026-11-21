import base64
import hmac
import json
import subprocess
from contextlib import asynccontextmanager
from typing import Literal
from urllib.parse import quote

import httpx
from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel, ValidationError
from sqlalchemy import select, text

from . import workflow as wf
from .contracts import Action, Callback, Correction, Submission, Upload
from .db import Case, Document, Receipt, Session
from .evidence import Problem
from .settings import (
    FIXTURES,
    LEGAL_NAME,
    NEW_GST,
    OLD_GST,
    PAN,
    SIMULATOR_URL,
    STATIC,
    internal_headers,
    secret,
    sign,
)
from .views import detail, summary

PERSONAS = {
    "operator": {"role": "operator", "owner": "ops", "name": "Demo operator"},
    "merchant": {"role": "merchant", "owner": "riverbend", "name": LEGAL_NAME},
    "other_merchant": {"role": "merchant", "owner": "second-merchant", "name": "Second demo merchant"},
}
FILES = {
    "pan.json",
    "gst-inactive.pdf",
    "gst-current.pdf",
    "gst-scan.png",
    "gst-scan-alternate.png",
    "gst-scan-obscured.png",
    "registry-letter.jpg",
}


def seed():
    with Session.begin() as db:
        if db.scalar(
            select(Case.id).where(
                Case.owner == "riverbend", Case.submission_key == "riverbend-introduction"
            )
        ):
            return
        wf.submit(
            db,
            "riverbend",
            Submission(
                submission_key="riverbend-introduction",
                legal_name=LEGAL_NAME,
                pan=PAN,
                gstin=OLD_GST,
                documents=[
                    Upload(
                        kind="PAN",
                        filename="pan.json",
                        fields=json.loads((FIXTURES / "pan.json").read_text()),
                    ),
                    Upload(
                        kind="GST",
                        filename="gst-inactive.pdf",
                        content_base64=base64.b64encode(
                            (FIXTURES / "gst-inactive.pdf").read_bytes()
                        ).decode(),
                    ),
                ],
            ),
        )


@asynccontextmanager
async def lifespan(_):
    seed()
    yield


app = FastAPI(title="Merchant desk API", version="1.0", lifespan=lifespan)


@app.exception_handler(Problem)
async def problem(_, exc):
    return JSONResponse({"detail": exc.detail}, status_code=exc.status)


@app.exception_handler(RequestValidationError)
async def bad_input(_, exc):
    errors = [" / ".join(str(x) for x in e["loc"][1:]) + " · " + e["msg"] for e in exc.errors()]
    return JSONResponse({"detail": " | ".join(errors)[:700]}, status_code=422)


def signer():
    return URLSafeTimedSerializer(secret("session"), salt="merchant-desk")


def identify(request: Request):
    try:
        return PERSONAS[signer().loads(request.cookies.get("desk_session", ""), max_age=86400)]
    except (BadSignature, KeyError):
        raise Problem(401, "Open a demo session to access this case.")


def set_persona(response, key):
    response.set_cookie(
        "desk_session", signer().dumps(key), httponly=True, samesite="strict", max_age=86400
    )
    response.headers["Cache-Control"] = "no-store"
    return PERSONAS[key]


class Persona(BaseModel):
    persona: Literal["operator", "merchant", "other_merchant"]


@app.get("/api/session")
def session_info(request: Request, response: Response):
    try:
        return identify(request)
    except Problem:
        return set_persona(response, "operator")


@app.post("/api/demo/session")
def select_persona(body: Persona, response: Response):
    return set_persona(response, body.persona)


def operator(user=Depends(identify)):
    if user["role"] != "operator":
        raise Problem(403, "Operator access required.")
    return user


def merchant(user=Depends(identify)):
    if user["role"] != "merchant":
        raise Problem(403, "Open the merchant portal to submit evidence.")
    return user


def check_access(case, user):
    if case is None or (user["role"] != "operator" and case.owner != user["owner"]):
        raise Problem(404, "Application not found.")


@app.get("/api/health")
def health():
    with Session() as db:
        db.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.get("/api/demo/fixtures")
def fixtures():
    return {
        "legal_name": LEGAL_NAME,
        "pan": PAN,
        "old_gstin": OLD_GST,
        "current_gstin": NEW_GST,
        "files": sorted(FILES),
    }


@app.get("/api/demo/files/{filename}")
def sample(filename: str):
    if filename not in FILES:
        raise Problem(404, "Sample not found.")
    return FileResponse(FIXTURES / filename, filename=filename)


@app.post("/api/applications", status_code=201)
def create(body: Submission, user=Depends(merchant)):
    with Session.begin() as db:
        return detail(db, wf.submit(db, user["owner"], body), False)


@app.get("/api/applications")
def my_cases(user=Depends(merchant)):
    with Session() as db:
        return [
            summary(c)
            for c in db.scalars(
                select(Case).where(Case.owner == user["owner"]).order_by(Case.created_at.desc())
            )
        ]


@app.get("/api/applications/{case_id}")
def case_details(case_id: str, user=Depends(identify)):
    with Session() as db:
        case = db.get(Case, case_id)
        check_access(case, user)
        return detail(db, case, user["role"] == "operator")


@app.post("/api/applications/{case_id}/corrections")
def correction(case_id: str, body: Correction, user=Depends(merchant)):
    with Session.begin() as db:
        case = wf.locked(db, case_id)
        check_access(case, user)
        wf.correct(db, case, body)
        return detail(db, case, False)


def document_data(case_id, doc_id, user):
    with Session() as db:
        check_access(db.get(Case, case_id), user)
        doc = db.get(Document, doc_id)
        if doc is None or doc.case_id != case_id:
            raise Problem(404, "Document not found.")
        return doc.data, doc.mime, doc.filename


@app.get("/api/applications/{case_id}/documents/{doc_id}")
def source(case_id: str, doc_id: str, user=Depends(identify)):
    data, mime, name = document_data(case_id, doc_id, user)
    return Response(
        data,
        media_type=mime,
        headers={
            "Content-Disposition": "inline; filename*=UTF-8''" + quote(name, safe=""),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/api/applications/{case_id}/documents/{doc_id}/preview")
def preview(case_id: str, doc_id: str, page: int = 1, user=Depends(identify)):
    data, mime, _ = document_data(case_id, doc_id, user)
    if mime == "application/pdf":
        if page < 1 or page > 8:
            raise Problem(422, "Invalid page.")
        try:
            run = subprocess.run(
                [
                    "pdftoppm",
                    "-f",
                    str(page),
                    "-l",
                    str(page),
                    "-singlefile",
                    "-scale-to",
                    "1000",
                    "-png",
                    "-",
                ],
                input=data,
                capture_output=True,
                timeout=8,
                check=True,
            )
            data, mime = run.stdout, "image/png"
        except (OSError, subprocess.SubprocessError):
            raise Problem(422, "Preview unavailable. Open the original document.")
    if not mime.startswith("image/") or not data:
        raise Problem(422, "This document has no image preview.")
    return Response(data, media_type=mime, headers={"Cache-Control": "no-store"})


@app.get("/api/operator/cases", dependencies=[Depends(operator)])
def queue(state: str | None = None):
    with Session() as db:
        query = select(Case).order_by(Case.created_at.desc())
        if state:
            query = query.where(Case.state == state.upper())
        return [summary(c) for c in db.scalars(query)]


@app.get("/api/operator/cases/{case_id}", dependencies=[Depends(operator)])
def operator_case(case_id: str):
    with Session() as db:
        case = db.get(Case, case_id)
        if not case:
            raise Problem(404, "Application not found.")
        return detail(db, case, True)


@app.post("/api/operator/cases/{case_id}/actions", dependencies=[Depends(operator)])
def act(case_id: str, body: Action):
    with Session.begin() as db:
        case = wf.locked(db, case_id)
        wf.act(db, case, body)
        return detail(db, case, True)


@app.post("/api/operator/cases/{case_id}/replay", status_code=202, dependencies=[Depends(operator)])
def replay(case_id: str):
    with Session() as db:
        receipt = db.scalar(
            select(Receipt)
            .where(Receipt.case_id == case_id, Receipt.disposition == "applied")
            .order_by(Receipt.received_at.desc())
            .limit(1)
        )
        if not receipt:
            raise Problem(409, "Wait for a provider callback before replaying.")
        attempt_id = receipt.attempt_id
    try:
        response = httpx.post(
            f"{SIMULATOR_URL}/replay/{attempt_id}", headers=internal_headers(), timeout=5
        )
        response.raise_for_status()
    except httpx.HTTPError:
        raise Problem(503, "Simulator unavailable. Try the replay again.")
    return {"queued": True}


@app.post("/api/webhooks/{provider}")
async def webhook(provider: str, request: Request):
    provider = provider.upper()
    raw = await request.body()
    if provider not in {"PAN", "GST"} or not hmac.compare_digest(
        request.headers.get("X-Webhook-Signature", ""), sign(provider, raw)
    ):
        raise Problem(401, "Invalid provider signature.")
    try:
        body = Callback.model_validate_json(raw)
    except ValidationError:
        raise Problem(422, "Invalid callback payload.")
    with Session.begin() as db:
        return {"disposition": wf.receive(db, provider, body)}


if STATIC.is_dir():
    app.mount("/", StaticFiles(directory=STATIC, html=True), name="web")
