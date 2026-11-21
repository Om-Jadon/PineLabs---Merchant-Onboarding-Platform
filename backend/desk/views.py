from sqlalchemy import select
from . import workflow as wf
from .db import Attempt, Document, Event, Receipt
from .evidence import compare

ACTORS = {
    "SUBMITTED": "Document reader",
    "VERIFYING": "Verification providers",
    "IN_REVIEW": "Operator",
    "NEEDS_INFO": "Merchant",
    "APPROVED": "Merchant registry",
    "ACTIVE": "Complete",
    "REJECTED": "Closed",
}


def summary(case):
    return {
        "id": case.id,
        "reference": "APP-" + case.id[:8].upper(),
        "legal_name": case.legal_name or "Unnamed merchant",
        "state": case.state,
        "reason": case.reason,
        "next_actor": "Operator" if case.issuance == "TECHNICAL" else ACTORS[case.state],
        "version": case.version,
        "created_at": case.created_at,
        "state_since": case.state_since,
        "mid": case.mid,
    }


def detail(db, case, operator):
    db.flush()
    docs = list(
        db.scalars(
            select(Document).where(Document.case_id == case.id).order_by(Document.created_at.desc())
        )
    )
    latest = wf.documents(db, case)
    checks = list(
        db.scalars(select(Attempt).where(Attempt.case_id == case.id).order_by(Attempt.created_at))
    )
    events = list(db.scalars(select(Event).where(Event.case_id == case.id).order_by(Event.id)))
    receipts = list(
        db.scalars(select(Receipt).where(Receipt.case_id == case.id).order_by(Receipt.received_at))
    )
    result = {
        **summary(case),
        "declared": {"legal_name": case.legal_name, "pan": case.pan, "gstin": case.gstin},
        "policy": case.policy,
        "issuance": case.issuance,
        "requested_kind": case.requested_kind,
        "request_note": case.request_note,
        "scenario": case.scenario,
        "actions": wf.actions(db, case) if operator else [],
        "can_correct": not operator and case.state not in wf.TERMINAL,
        "documents": [
            {
                "id": d.id,
                "kind": d.kind,
                "revision": d.revision,
                "filename": d.filename,
                "mime": d.mime,
                "detected_kind": d.detected_kind,
                "status": d.status,
                "method": d.method,
                "note": d.reader_note,
                "fields": d.fields,
                "current": latest.get(d.kind) == d or (d.kind == "AUTO" and d.status != "SUPERSEDED"),
            }
            for d in docs
        ],
        "fields": compare(case, latest),
        "attempts": [
            {
                "id": a.id,
                "kind": a.kind,
                "number": a.number,
                "status": a.status,
                "reason": a.reason,
                "current": a.is_current,
                "deadline": a.deadline,
                "created_at": a.created_at,
                "provider_ref": a.provider_ref,
            }
            for a in checks
        ],
        "events": [
            {
                "id": e.id,
                "at": e.at,
                "actor": e.actor,
                "title": e.title,
                "detail": e.detail,
                "source_id": e.source_id,
            }
            for e in events
            if operator or e.actor != "Webhook"
        ],
        "receipts": [
            {
                "event_id": r.event_id,
                "kind": r.provider,
                "attempt_id": r.attempt_id,
                "received_at": r.received_at,
                "disposition": r.disposition,
                "deliveries": r.deliveries,
            }
            for r in receipts
        ]
        if operator
        else [],
    }
    return result
