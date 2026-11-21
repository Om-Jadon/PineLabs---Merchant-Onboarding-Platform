import hashlib
import json
import re
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .contracts import Action, Callback, Correction, Submission
from .db import Attempt, Case, Document, Event, Job, Receipt, uid
from .evidence import PATTERNS, Problem, compare, make_document, normalize
from .settings import CHECK_TIMEOUT, now

TERMINAL = {"APPROVED", "ACTIVE", "REJECTED"}
REASONS = {
    "PAN_MATCH": "PAN identity matched",
    "GST_ACTIVE": "GST registration active",
    "GST_INACTIVE": "GST registration inactive",
    "RECORD_MISMATCH": "Provider record does not match",
}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def masked(value):
    return "•••• " + value[-4:] if len(value) >= 4 else "••••"


def redact(note):
    for key in ("gstin", "pan"):
        note = re.sub(PATTERNS[key].pattern, lambda m: masked(m[0]), note, flags=re.I)
    return note


def locked(db: Session, case_id: str):
    case = db.scalar(select(Case).where(Case.id == case_id).with_for_update())
    if not case:
        raise Problem(404, "Application not found.")
    return case


def documents(db, case):
    records = db.scalars(
        select(Document)
        .where(
            Document.case_id == case.id, Document.kind.in_(case.policy), Document.status != "SUPERSEDED"
        )
        .order_by(Document.revision)
    )
    return {d.kind: d for d in records}


def unclassified(db, case):
    return list(
        db.scalars(
            select(Document)
            .where(Document.case_id == case.id, Document.kind == "AUTO", Document.status != "SUPERSEDED")
            .order_by(Document.created_at)
        )
    )


def current(db, case):
    return {
        a.kind: a
        for a in db.scalars(select(Attempt).where(Attempt.case_id == case.id, Attempt.is_current))
    }


def record(db, case, actor, title, detail="", source=None):
    db.add(Event(case_id=case.id, actor=actor, title=title, detail=redact(detail), source_id=source))


def change_state(db, case, state, reason):
    if case.state != state:
        record(db, case, "Workflow", f"{case.state} → {state}", reason)
        case.state, case.state_since = state, now()
    case.reason = reason


def enqueue(db, case, kind, source, key, at=None):
    db.add(Job(case_id=case.id, kind=kind, source=source, operation_key=key, at=at or now()))


def fingerprint(case, kind, docs):
    values = [normalize(case.legal_name), normalize(case.pan), docs[kind].id if kind in docs else None]
    if kind == "GST":
        values.append(normalize(case.gstin))
    return digest(values)


def new_attempt(db, case, kind, key, retry=0):
    last = (
        db.scalar(
            select(func.max(Attempt.number)).where(Attempt.case_id == case.id, Attempt.kind == kind)
        )
        or 0
    )
    a = Attempt(
        id=uid(),
        case_id=case.id,
        kind=kind,
        fingerprint=key,
        number=last + 1,
        retry=retry,
    )
    db.add(a)
    enqueue(db, case, "dispatch", a.id, "dispatch/" + a.id)
    record(db, case, "Workflow", f"{kind} verification queued", f"Attempt {a.number}", a.id)
    return a


def begin_dispatch(db, case, attempt):
    if not attempt.is_current or attempt.status != "PENDING" or case.state in TERMINAL:
        return False
    if attempt.deadline is None:
        attempt.deadline = now() + timedelta(seconds=CHECK_TIMEOUT)
        db.execute(
            insert(Job)
            .values(
                case_id=case.id,
                kind="timeout",
                source=attempt.id,
                operation_key="timeout/" + attempt.id,
                at=attempt.deadline,
            )
            .on_conflict_do_update(
                index_elements=["operation_key"],
                set_={
                    "at": attempt.deadline,
                    "status": "READY",
                    "lease_token": None,
                    "lease_until": None,
                },
            )
        )
    return now() < attempt.deadline


def awaiting_replacement(db, case, docs, loose):
    kind = case.requested_kind
    wanted = docs.get(kind)
    incoming = [d for d in [*loose, wanted] if d is not None and d.created_at >= case.requested_at]
    if (
        wanted in incoming
        and wanted.status == "READY"
        and all(row["status"] == "matched" for row in compare(case, docs) if row["kind"] == kind)
    ):
        record(
            db,
            case,
            "Workflow",
            f"Requested {kind} evidence received",
            f"Revision {wanted.revision}",
            wanted.id,
        )
        case.requested_kind = case.request_note = case.requested_at = None
        return False
    if any(d.status in {"TECHNICAL", "CONFLICT"} for d in incoming):
        change_state(db, case, "IN_REVIEW", f"Replacement {kind} evidence needs review")
    elif any(d.status == "PENDING" for d in incoming):
        change_state(db, case, "SUBMITTED", "Reading replacement evidence")
    else:
        change_state(db, case, "NEEDS_INFO", f"Current {kind} evidence requested")
    return True


def derive(db, case):
    """Called while holding the application row lock. Decisions and their jobs commit together."""
    if case.state in TERMINAL:
        return
    docs = documents(db, case)
    checks = current(db, case)
    for kind, a in list(checks.items()):
        if a.fingerprint != fingerprint(case, kind, docs):
            a.is_current = False
            checks.pop(kind)
            record(
                db, case, "Workflow", f"{kind} evidence changed", f"Attempt {a.number} superseded", a.id
            )
    db.flush()
    loose = unclassified(db, case)
    if case.requested_kind and awaiting_replacement(db, case, docs, loose):
        return
    incomplete = next((d for d in loose if d.status != "PENDING"), None)
    if incomplete:
        needs_info = incomplete.status == "UNREADABLE"
        change_state(
            db,
            case,
            "NEEDS_INFO" if needs_info else "IN_REVIEW",
            incomplete.reader_note or "Uploaded document needs identification",
        )
        return
    if loose or any(d.status == "PENDING" for d in docs.values()):
        change_state(db, case, "SUBMITTED", "Reading uploaded evidence")
        return
    rows = compare(case, docs)
    for statuses, state in (
        ({"technical", "conflicting"}, "IN_REVIEW"),
        ({"missing", "invalid", "unreadable"}, "NEEDS_INFO"),
    ):
        bad = next((r for r in rows if r["status"] in statuses), None)
        if bad:
            reason = f"{bad['kind']} · {bad['label']} {bad['status']}"
            if bad["status"] == "technical":
                reason = f"{bad['kind']} document reader needs a retry"
            change_state(db, case, state, reason)
            return
    if any(r["status"] == "pending" for r in rows):
        change_state(db, case, "SUBMITTED", "Reading the uploaded scan")
        return
    for kind in case.policy:
        if kind not in checks:
            checks[kind] = new_attempt(db, case, kind, fingerprint(case, kind, docs))
    failed = next((a for a in checks.values() if a.status in {"FAILED", "TECHNICAL", "TIMED_OUT"}), None)
    if failed:
        change_state(db, case, "IN_REVIEW", failed.reason)
    elif case.policy and all(checks[k].status == "PASSED" for k in case.policy):
        change_state(db, case, "APPROVED", "Checks complete · issuing merchant ID")
        case.issuance = "PENDING"
        enqueue(db, case, "issue", case.id, "issue/" + case.id)
    else:
        change_state(db, case, "VERIFYING", "Waiting for verification callbacks")


def attach(db, case, uploads):
    for upload in uploads:
        previous = (
            db.scalar(
                select(func.max(Document.revision)).where(
                    Document.case_id == case.id, Document.kind == upload.kind
                )
            )
            or 0
        )
        doc = make_document(upload, case.id, previous + 1)
        db.add(doc)
        db.flush()
        if doc.status == "PENDING":
            enqueue(db, case, "extract", doc.id, "extract/" + doc.id)
        record(
            db,
            case,
            "Merchant",
            f"{doc.kind} evidence received",
            f"Revision {doc.revision} · {doc.method}",
            doc.id,
        )


def apply_reading(db, case, doc, reading):
    if doc.status != "PENDING":
        return
    known = documents(db, case)
    doc.detected_kind, doc.method, doc.reader_note = reading.kind, reading.method, reading.note or None
    if case.state in TERMINAL or (doc.kind != "AUTO" and known.get(doc.kind) != doc):
        doc.status = "SUPERSEDED"
        return
    if doc.kind == "AUTO" and reading.kind in case.policy:
        previous = known.get(reading.kind)
        if previous and previous.created_at > doc.created_at:
            doc.status = "SUPERSEDED"
            case.version += 1
            record(
                db,
                case,
                "Document reader",
                "Earlier evidence superseded",
                "A newer revision is current",
                doc.id,
            )
            derive(db, case)
            return
        doc.kind = reading.kind
        doc.revision = (
            db.scalar(
                select(func.max(Document.revision))
                .where(Document.case_id == case.id, Document.kind == reading.kind, Document.id != doc.id)
                .execution_options(autoflush=False)
            )
            or 0
        ) + 1
    doc.status, doc.fields = reading.status, reading.fields
    if reading.status == "READY" and reading.kind != doc.kind:
        doc.status, doc.reader_note = (
            "CONFLICT",
            f"Expected {doc.kind} evidence. Reader identified {reading.kind}.",
        )
    record(
        db,
        case,
        "Document reader",
        f"{doc.kind} evidence examined",
        f"{doc.method} · Revision {doc.revision}" + (f" · {doc.reader_note}" if doc.reader_note else ""),
        doc.id,
    )
    case.version += 1
    derive(db, case)


def submit(db, owner, submission: Submission):
    identity = digest(submission.model_dump())
    statement = insert(Case).values(
        owner=owner,
        submission_key=submission.submission_key,
        payload_hash=identity,
        legal_name=submission.legal_name,
        pan=normalize(submission.pan),
        gstin=normalize(submission.gstin),
        scenario=submission.scenario,
        policy=["PAN", "GST"],
    )
    new_id = db.scalar(
        statement.on_conflict_do_nothing(index_elements=["owner", "submission_key"]).returning(Case.id)
    )
    if not new_id:
        case = db.scalar(
            select(Case).where(Case.owner == owner, Case.submission_key == submission.submission_key)
        )
        if case.payload_hash != identity:
            raise Problem(409, "This submission key already belongs to different application data.")
        return case
    case = db.get(Case, new_id)
    record(db, case, "Merchant", "Application submitted", "Required checks · PAN and GST")
    attach(db, case, submission.documents)
    derive(db, case)
    return case


def check_version(case, expected):
    if case.version != expected:
        raise Problem(409, f"This case changed. Refresh and try again with version {case.version}.")


def correct(db, case, body: Correction):
    check_version(case, body.expected_version)
    if case.state in TERMINAL:
        raise Problem(409, "This application's decision is final.")
    changed = set()
    for field in ("legal_name", "pan", "gstin"):
        value = getattr(body, field)
        if value is None:
            continue
        value = value if field == "legal_name" else normalize(value)
        if getattr(case, field) != value:
            setattr(case, field, value)
            changed.add(field)
    if not changed and not body.documents:
        raise Problem(422, "Change a declared value or attach replacement evidence.")
    loose = unclassified(db, case)
    replacement_ids = body.replace_document_ids
    if replacement_ids is None:
        # Legacy clients can implicitly replace an unclassified file with the same unique filename.
        names = {upload.filename for upload in body.documents}
        replacement_ids = [
            d.id
            for d in loose
            if d.filename in names and sum(other.filename == d.filename for other in loose) == 1
        ]
    targets = set(replacement_ids)
    if (
        len(targets) != len(replacement_ids)
        or len(targets) > len(body.documents)
        or not targets <= {d.id for d in loose}
    ):
        raise Problem(409, "Choose a current unclassified upload for each replacement.")
    for doc in loose:
        if doc.id in targets:
            doc.status = "SUPERSEDED"
    attach(db, case, body.documents)
    affected = {d.kind for d in body.documents}
    if changed & {"legal_name", "pan"}:
        affected.update(case.policy)
    if "gstin" in changed:
        affected.add("GST")
    case.version += 1
    record(db, case, "Merchant", "Correction submitted", "Rechecking " + " and ".join(sorted(affected)))
    change_state(db, case, "SUBMITTED", "Validating corrected evidence")
    derive(db, case)


def expire(db, case, attempt):
    if (
        not attempt.is_current
        or attempt.status != "PENDING"
        or case.state in TERMINAL
        or attempt.deadline is None
        or now() < attempt.deadline
    ):
        return
    attempt.status = "TIMED_OUT"
    attempt.reason = f"{attempt.kind} provider callback timed out"
    record(db, case, "Workflow", attempt.reason, f"Attempt {attempt.number}", attempt.id)
    if attempt.retry < 2:
        attempt.is_current = False
        db.flush()
        new_attempt(db, case, attempt.kind, attempt.fingerprint, attempt.retry + 1)
    else:
        job = db.scalar(select(Job).where(Job.operation_key == "timeout/" + attempt.id))
        if job:
            job.status, job.error = "DEAD", attempt.reason
    case.version += 1
    derive(db, case)


def receive(db, provider, body: Callback):
    if provider not in {"PAN", "GST"}:
        raise Problem(404, "Unknown provider.")
    case_id = db.scalar(
        select(Attempt.case_id).where(Attempt.id == body.attempt_id, Attempt.kind == provider)
    )
    if not case_id:
        raise Problem(404, "Unknown verification attempt.")
    case = locked(db, case_id)
    attempt = db.get(Attempt, body.attempt_id)
    if body.provider_ref != (attempt.provider_ref or f"{provider}-{attempt.id}"):
        raise Problem(409, "Provider reference mismatch.")
    if attempt.is_current and attempt.status == "PENDING" and attempt.deadline is None:
        raise Problem(409, "Verification has not been dispatched.")
    allowed = (
        {"PAN": {"PAN_MATCH"}, "GST": {"GST_ACTIVE"}}[provider]
        if body.outcome == "PASSED"
        else ({"RECORD_MISMATCH", "GST_INACTIVE"} if provider == "GST" else {"RECORD_MISMATCH"})
    )
    if body.reason not in allowed:
        raise Problem(422, "Outcome and reason are inconsistent.")
    hashed = digest(body.model_dump())
    received = now()
    reserved = db.scalar(
        insert(Receipt)
        .values(
            provider=provider,
            event_id=body.event_id,
            case_id=case_id,
            attempt_id=attempt.id,
            disposition="received",
            digest=hashed,
            received_at=received,
        )
        .on_conflict_do_nothing(index_elements=["provider", "event_id"])
        .returning(Receipt.event_id)
    )
    receipt = db.get(Receipt, (provider, body.event_id))
    if reserved is None:
        if receipt.digest != hashed:
            raise Problem(409, "Event ID already has a different result.")
        receipt.deliveries += 1
        record(
            db,
            case,
            "Webhook",
            f"{provider} callback replay acknowledged",
            "Original result already applied or recorded",
            attempt.id,
        )
        return "duplicate"
    if attempt.status == "TIMED_OUT" or (
        attempt.status == "PENDING" and attempt.deadline and received >= attempt.deadline
    ):
        disposition = "late"
        expire(db, case, attempt)
    elif not attempt.is_current or case.state in TERMINAL or attempt.status != "PENDING":
        disposition = "superseded"
    else:
        disposition = "applied"
        attempt.status, attempt.reason, attempt.provider_ref = (
            body.outcome,
            REASONS[body.reason],
            body.provider_ref,
        )
        case.version += 1
        record(
            db,
            case,
            provider + " provider",
            attempt.reason,
            f"Callback received · Attempt {attempt.number}",
            attempt.id,
        )
        derive(db, case)
    receipt.disposition = disposition
    if disposition != "applied":
        record(
            db,
            case,
            "Webhook",
            f"{provider} callback recorded as {disposition}",
            f"Attempt {attempt.number}",
            attempt.id,
        )
    return disposition


def actions(db, case):
    if case.state == "APPROVED" and case.issuance == "TECHNICAL":
        return ["retry"]
    if case.state != "IN_REVIEW":
        return []
    available = ["request_info", "reject"]
    if any(a.status in {"TECHNICAL", "TIMED_OUT"} for a in current(db, case).values()) or any(
        d.status == "TECHNICAL" for d in [*documents(db, case).values(), *unclassified(db, case)]
    ):
        available.insert(0, "retry")
    return available


def act(db, case, body: Action):
    check_version(case, body.expected_version)
    if body.action not in actions(db, case):
        raise Problem(409, "This action is unavailable in the current state.")
    case.version += 1
    if body.action == "reject":
        record(db, case, "Operator", "Application rejected", body.reason)
        change_state(db, case, "REJECTED", redact(body.reason))
    elif body.action == "request_info":
        case.requested_kind = body.kind or "GST"
        case.request_note = redact(body.reason)
        case.requested_at = now()
        record(db, case, "Operator", f"Current {case.requested_kind} evidence requested", body.reason)
        derive(db, case)
    else:
        record(db, case, "Operator", "Technical retry requested", body.reason)
        if case.state == "APPROVED":
            case.issuance, case.reason = "PENDING", "Retrying merchant ID issuance"
            enqueue(db, case, "issue", case.id, "issue/" + uid())
            return
        for a in current(db, case).values():
            if a.status in {"TECHNICAL", "TIMED_OUT"}:
                a.is_current = False
        for d in [*documents(db, case).values(), *unclassified(db, case)]:
            if d.status == "TECHNICAL":
                d.status = "PENDING"
                enqueue(db, case, "extract", d.id, "extract/" + uid())
        db.flush()
        derive(db, case)
