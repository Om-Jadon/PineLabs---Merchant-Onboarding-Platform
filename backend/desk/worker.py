import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import httpx
from psycopg.errors import UniqueViolation
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError

from . import workflow as wf
from .contracts import IssueReply, VerificationReply
from .db import Attempt, Case, Document, Job, Session, uid
from .gemini import read_document
from .settings import SIMULATOR_URL, internal_headers, now


def claim(documents_only=None):
    timestamp = now()
    with Session.begin() as db:
        query = select(Job).where(
            Job.at <= timestamp,
            or_(Job.status == "READY", and_(Job.status == "RUNNING", Job.lease_until < timestamp)),
        )
        if documents_only is not None:
            query = query.where(Job.kind == "extract" if documents_only else Job.kind != "extract")
        job = db.scalar(query.order_by(Job.at, Job.id).limit(1).with_for_update(skip_locked=True))
        if not job:
            return None
        job.status, job.lease_token, job.lease_until = (
            "RUNNING",
            uid(),
            timestamp + timedelta(seconds=60),
        )
        job.tries += 1
        return job.id, job.case_id, job.lease_token


def perform(job_id):
    with Session.begin() as db:
        job = db.get(Job, job_id)
        kind, source = job.kind, job.source
        if kind == "timeout":
            return None
        case = wf.locked(db, job.case_id) if kind == "dispatch" else db.get(Case, job.case_id)
        if kind == "issue":
            if case.state != "APPROVED" or case.mid:
                return None
            path = "/merchants"
            body = {"case_id": case.id, "scenario": case.scenario}
        elif kind == "dispatch":
            a = db.get(Attempt, source)
            if not wf.begin_dispatch(db, case, a):
                return None
            path = f"/{a.kind.lower()}/verifications"
            body = {
                "case_id": case.id,
                "attempt_id": a.id,
                "legal_name": case.legal_name,
                "pan": case.pan,
                "gstin": case.gstin if a.kind == "GST" else "",
                "scenario": case.scenario,
                "number": a.number,
            }
        elif kind == "extract":
            doc = db.get(Document, source)
            if doc.status != "PENDING" or case.state in wf.TERMINAL:
                return None
            data, mime = doc.data, doc.mime
        else:
            raise ValueError("Unknown job type")
    if kind == "extract":
        return read_document(data, mime)
    response = httpx.post(SIMULATOR_URL + path, json=body, headers=internal_headers(), timeout=5)
    response.raise_for_status()
    if kind == "issue":
        return IssueReply.model_validate(response.json()).model_dump()
    reply = VerificationReply.model_validate(response.json())
    if reply.provider_ref != f"{a.kind}-{source}":
        raise ValueError("Verification response does not identify the requested attempt")
    return reply.model_dump()


def failed(db, job, case, error):
    if job.kind == "dispatch":
        attempt = db.get(Attempt, job.source)
        obsolete = case.state in wf.TERMINAL or not attempt.is_current or attempt.status != "PENDING"
    elif job.kind == "extract":
        doc = db.get(Document, job.source)
        obsolete = (
            case.state in wf.TERMINAL
            or doc.status != "PENDING"
            or (doc.kind != "AUTO" and wf.documents(db, case).get(doc.kind) != doc)
        )
    else:
        obsolete = job.kind == "issue" and (case.state != "APPROVED" or case.mid is not None)
    if obsolete:
        job.status = "DONE"
        return
    job.error = error
    if job.tries < 3:
        job.status, job.at = "READY", now() + timedelta(seconds=2 ** (job.tries - 1))
        if job.kind == "issue" and case.state == "APPROVED":
            case.issuance, case.reason = "RETRYING", "Registry response pending · retry scheduled"
        wf.record(
            db,
            case,
            "Worker",
            f"{job.kind.capitalize()} retry scheduled",
            f"Delivery {job.tries} · {error}",
            job.source,
        )
        return
    job.status = "DEAD"
    if job.kind == "dispatch":
        a = db.get(Attempt, job.source)
        if a.is_current and a.status == "PENDING":
            a.status, a.reason = "TECHNICAL", f"{a.kind} provider unavailable"
    elif job.kind == "extract":
        doc = db.get(Document, job.source)
        if doc.status == "PENDING":
            doc.status, doc.reader_note = "TECHNICAL", "Document reader unavailable"
    elif job.kind == "issue" and case.state == "APPROVED":
        case.issuance, case.reason = "TECHNICAL", "Merchant ID issuance needs a retry"
    case.version += 1
    wf.record(
        db, case, "Worker", "Retry limit reached", f"{job.kind} retained for operator review", job.source
    )
    wf.derive(db, case)


def finish(claimed, result, error):
    job_id, case_id, token = claimed
    with Session.begin() as db:
        case = wf.locked(db, case_id)
        job = db.get(Job, job_id, with_for_update=True)
        if job.status != "RUNNING" or job.lease_token != token:
            return
        job.lease_token = job.lease_until = None
        if error:
            failed(db, job, case, error)
            return
        job.status = "DONE"
        if job.kind == "timeout":
            wf.expire(db, case, db.get(Attempt, job.source))
        elif result is not None and job.kind == "dispatch":
            db.get(Attempt, job.source).provider_ref = result["provider_ref"]
        elif result is not None and job.kind == "extract":
            wf.apply_reading(db, case, db.get(Document, job.source), result)
            if result.status == "TECHNICAL":
                job.status, job.error = "DEAD", result.note
        elif result is not None and job.kind == "issue" and case.state == "APPROVED":
            try:
                with db.begin_nested():
                    case.mid = result["mid"]
                    db.flush()
            except IntegrityError as exc:
                if (
                    not isinstance(exc.orig, UniqueViolation)
                    or exc.orig.diag.constraint_name != "cases_mid_key"
                ):
                    raise
                failed(db, job, case, "Registry returned a merchant ID already assigned to another case")
                return
            case.issuance = "ISSUED"
            case.version += 1
            wf.record(db, case, "Merchant registry", "Merchant ID issued", case.mid)
            wf.change_state(db, case, "ACTIVE", "Merchant ID issued")


def run_once(documents_only=None):
    claimed = claim(documents_only)
    if not claimed:
        return False
    try:
        result = perform(claimed[0])
    except Exception as exc:
        error = (
            f"HTTP {exc.response.status_code}"
            if isinstance(exc, httpx.HTTPStatusError)
            else type(exc).__name__
        )
        finish(claimed, None, error)
    else:
        finish(claimed, result, None)
    return True


def consume(documents_only):
    while True:
        try:
            busy = run_once(documents_only)
        except Exception as error:
            print("Worker transaction:", type(error).__name__, flush=True)
            busy = False
        if not busy:
            time.sleep(0.25)


if __name__ == "__main__":
    # A slow model request should never hold up a verification dispatch or MID retry.
    with ThreadPoolExecutor(max_workers=1) as extractors:
        extractors.submit(consume, True)
        consume(False)
