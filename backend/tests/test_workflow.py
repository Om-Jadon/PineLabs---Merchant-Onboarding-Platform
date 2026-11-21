import base64
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from desk import workflow as wf
from desk import worker
from desk.api import app
from desk.contracts import Action, Callback, Correction, IssueInput, Submission, Upload
from desk.db import Attempt, Case, Event, Job, Merchant, Receipt, Session, uid
from desk.evidence import Problem, Reading, local_read
from desk.settings import FIXTURES, LEGAL_NAME, NEW_GST, OLD_GST, PAN, now, sign
from desk.simulator import issue

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"), reason="Requires an explicitly selected test PostgreSQL database"
)


def payload(gstin=NEW_GST, scenario="standard"):
    return Submission(
        submission_key=uid(),
        legal_name=LEGAL_NAME,
        pan=PAN,
        gstin=gstin,
        scenario=scenario,
        documents=[
            Upload(kind="PAN", filename="pan.json", fields={"holder_name": LEGAL_NAME, "pan": PAN}),
            Upload(kind="GST", filename="gst.json", fields={"legal_name": LEGAL_NAME, "gstin": gstin}),
        ],
    )


def application(submission=None):
    with Session.begin() as db:
        case = wf.submit(db, "test-" + uid(), submission or payload())
        return case.id


def callback(attempt, passed=True):
    return Callback(
        event_id=uid(),
        attempt_id=attempt.id,
        provider_ref=f"{attempt.kind}-{attempt.id}",
        outcome="PASSED" if passed else "FAILED",
        reason=("PAN_MATCH" if attempt.kind == "PAN" else "GST_ACTIVE") if passed else "GST_INACTIVE",
    )


def post(kind, body):
    with Session.begin() as db:
        return wf.receive(db, kind, body)


def current(case_id):
    with Session() as db:
        return wf.current(db, db.get(Case, case_id))


def test_partial_pass_correction_replay_and_terminal_guard():
    case_id = application(payload(OLD_GST))
    original = current(case_id)
    assert post("PAN", callback(original["PAN"])) == "applied"
    with Session() as db:
        assert db.get(Case, case_id).state == "VERIFYING"
    assert post("GST", callback(original["GST"], False)) == "applied"
    with Session.begin() as db:
        case = wf.locked(db, case_id)
        assert case.state == "IN_REVIEW" and case.mid is None
        wf.act(
            db,
            case,
            Action(
                action="request_info",
                expected_version=case.version,
                kind="GST",
                reason="Please replace the certificate.",
            ),
        )
        assert case.state == "NEEDS_INFO"
        wf.correct(
            db,
            case,
            Correction(expected_version=case.version, gstin=NEW_GST, documents=payload().documents[1:]),
        )
        assert case.state == "VERIFYING"
    updated = current(case_id)
    assert updated["PAN"].id == original["PAN"].id and updated["GST"].number == 2
    assert post("GST", callback(original["GST"])) == "superseded"
    good = callback(updated["GST"])
    assert post("GST", good) == "applied"
    assert post("GST", good) == "duplicate"
    with Session.begin() as db:
        case = wf.locked(db, case_id)
        assert case.state == "APPROVED"
        assert db.get(Receipt, ("GST", good.event_id)).deliveries == 2
        assert (
            db.scalar(select(func.count(Job.id)).where(Job.case_id == case_id, Job.kind == "issue")) == 1
        )
        with pytest.raises(Problem, match="final"):
            wf.correct(db, case, Correction(expected_version=case.version, gstin=OLD_GST))


def test_simultaneous_callbacks_produce_one_approval():
    case_id = application()
    attempts = current(case_id)
    start = Barrier(2)

    def send(kind):
        start.wait(timeout=5)
        return post(kind, callback(attempts[kind]))

    with ThreadPoolExecutor(2) as pool:
        assert list(pool.map(send, ["GST", "PAN"])) == ["applied", "applied"]
    with Session() as db:
        assert db.get(Case, case_id).state == "APPROVED"
        assert (
            db.scalar(
                select(func.count(Event.id)).where(
                    Event.case_id == case_id, Event.title.endswith("→ APPROVED")
                )
            )
            == 1
        )
        assert (
            db.scalar(select(func.count(Job.id)).where(Job.case_id == case_id, Job.kind == "issue")) == 1
        )


def test_deadline_enforced_on_receipt_and_bounded_retries():
    case_id = application()
    assert post("PAN", callback(current(case_id)["PAN"])) == "applied"
    for number in range(1, 4):
        attempt = current(case_id)["GST"]
        assert attempt.number == number
        with Session.begin() as db:
            db.get(Attempt, attempt.id).deadline = now() - timedelta(seconds=1)
        assert post("GST", callback(attempt)) == "late"
    with Session.begin() as db:
        case = wf.locked(db, case_id)
        assert case.state == "IN_REVIEW" and case.mid is None
        assert (
            db.scalar(select(Job.status).where(Job.operation_key == "timeout/" + attempt.id)) == "DEAD"
        )
        wf.act(
            db, case, Action(action="retry", expected_version=case.version, reason="Provider recovered.")
        )
        assert wf.current(db, case)["GST"].number == 4


def test_lost_registry_response_and_lease_recovery():
    case_id = application(payload(scenario="lost_mid_response"))
    for kind, attempt in current(case_id).items():
        assert post(kind, callback(attempt)) == "applied"
    with Session.begin() as db:
        job = db.scalar(select(Job).where(Job.case_id == case_id, Job.kind == "issue"))
        job.status, job.lease_token, job.lease_until = (
            "RUNNING",
            "expired-worker",
            now() - timedelta(seconds=3),
        )
        # An earlier due time isolates this job from other test applications' future timeouts.
        job.at = now() - timedelta(days=30)
        job_id = job.id
    first = worker.claim()
    assert first and first[0] == job_id
    body = IssueInput(case_id=case_id, scenario="lost_mid_response")
    with pytest.raises(HTTPException) as err:
        issue(body)
    assert err.value.status_code == 503
    worker.finish(first, None, "HTTP 503")
    with Session.begin() as db:
        job, case = db.get(Job, job_id), db.get(Case, case_id)
        assert job.status == "READY" and case.state == "APPROVED" and case.issuance == "RETRYING"
        job.at = now() - timedelta(days=30)
        issued = db.get(Merchant, case_id).mid
    second = worker.claim()
    assert second and second[0] == job_id
    worker.finish(first, {"mid": "stale-lease"}, None)
    worker.finish(second, issue(body), None)
    with Session() as db:
        case = db.get(Case, case_id)
        assert case.state == "ACTIVE" and case.mid == issued == issue(body)["mid"]
        assert db.scalar(select(func.count(Merchant.case_id)).where(Merchant.case_id == case_id)) == 1


def test_api_idempotency_roles_access_and_webhook_auth():
    with TestClient(app) as client:
        client.post("/api/demo/session", json={"persona": "merchant"})
        data = payload().model_dump()
        case = client.post("/api/applications", json=data)
        assert case.status_code == 201
        item = case.json()
        assert client.post("/api/applications", json=data).json()["id"] == item["id"]
        assert client.post("/api/applications", json={**data, "legal_name": "Other"}).status_code == 409
        path = "/api/applications/" + item["id"]
        assert (
            client.post(
                path + "/corrections", json={"expected_version": 99, "gstin": OLD_GST}
            ).status_code
            == 409
        )
        assert client.get("/api/operator/cases").status_code == 403
        client.post("/api/demo/session", json={"persona": "other_merchant"})
        assert client.get(path).status_code == 404
        assert client.get(path + "/documents/" + item["documents"][0]["id"]).status_code == 404
        assert (
            client.post(
                path + "/corrections", json={"expected_version": item["version"], "gstin": OLD_GST}
            ).status_code
            == 404
        )
        item_callback = callback(current(item["id"])["PAN"])
        raw = item_callback.model_dump_json().encode()
        assert (
            client.post(
                "/api/webhooks/pan", content=raw, headers={"X-Webhook-Signature": "bad"}
            ).status_code
            == 401
        )
        assert current(item["id"])["PAN"].status == "PENDING"
        headers = {"X-Webhook-Signature": sign("PAN", raw)}
        assert (
            client.post("/api/webhooks/pan", content=raw, headers=headers).json()["disposition"]
            == "applied"
        )
        assert (
            client.post("/api/webhooks/pan", content=raw, headers=headers).json()["disposition"]
            == "duplicate"
        )


def test_unspecified_document_revisions_and_unsupported_classification():
    original = payload()
    binary = Upload(
        filename="unnamed",
        content_base64=base64.b64encode((FIXTURES / "gst-current.pdf").read_bytes()).decode(),
    )
    original.documents = [original.documents[0], binary]
    case_id = application(original)
    with Session.begin() as db:
        case = wf.locked(db, case_id)
        stale = wf.unclassified(db, case)[0]
        assert case.state == "SUBMITTED"
        wf.correct(db, case, Correction(expected_version=case.version, documents=[binary]))
        latest = wf.unclassified(db, case)[0]
        assert stale.id != latest.id
        wf.apply_reading(db, case, stale, local_read(stale.data, stale.mime))
        assert stale.status == "SUPERSEDED" and case.state == "SUBMITTED"
        wf.apply_reading(
            db,
            case,
            latest,
            Reading("MIXED", {}, "Gemini · test", "CONFLICT", "Document type needs review"),
        )
        assert case.state == "IN_REVIEW" and not wf.current(db, case)
        wf.act(
            db,
            case,
            Action(
                action="request_info",
                kind="GST",
                expected_version=case.version,
                reason="Send one GST document",
            ),
        )
        wf.correct(db, case, Correction(expected_version=case.version, documents=[binary]))
        pending = wf.unclassified(db, case)[0]
        wf.apply_reading(db, case, pending, local_read(pending.data, pending.mime))
        assert case.requested_kind is None and case.state == "VERIFYING"
        assert wf.documents(db, case)["GST"].id == pending.id


def test_technical_failure_reading_requested_replacement_can_be_retried():
    case_id = application(payload(OLD_GST))
    assert post("GST", callback(current(case_id)["GST"], False)) == "applied"
    with Session.begin() as db:
        case = wf.locked(db, case_id)
        wf.act(
            db,
            case,
            Action(
                action="request_info", kind="GST", expected_version=case.version, reason="Replace GST"
            ),
        )
        binary = Upload(
            filename="letter.jpg",
            content_base64=base64.b64encode((FIXTURES / "registry-letter.jpg").read_bytes()).decode(),
        )
        wf.correct(db, case, Correction(expected_version=case.version, documents=[binary]))
        doc = wf.unclassified(db, case)[0]
        wf.apply_reading(
            db,
            case,
            doc,
            Reading("OTHER", {}, "Gemini + local reader", "TECHNICAL", "Gemini returned HTTP 429"),
        )
        assert case.state == "IN_REVIEW" and "retry" in wf.actions(db, case)
        wf.act(
            db, case, Action(action="retry", expected_version=case.version, reason="Reader recovered")
        )
        assert doc.status == "PENDING" and case.state == "SUBMITTED"
