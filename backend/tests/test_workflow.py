import base64
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
import httpx
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from desk import workflow as wf
from desk import simulator, worker
from desk.api import app
from desk.contracts import Action, Callback, Correction, IssueInput, Submission, Upload, VerifyInput
from desk.db import Attempt, Case, Delivery, Event, Job, Merchant, Receipt, Session, uid
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


def dispatched(case_id):
    with Session.begin() as db:
        case = wf.locked(db, case_id)
        checks = wf.current(db, case)
        for check in checks.values():
            wf.begin_dispatch(db, case, check)
        return checks


def test_partial_pass_correction_replay_and_terminal_guard():
    case_id = application(payload(OLD_GST))
    original = dispatched(case_id)
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
    updated = dispatched(case_id)
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
    attempts = dispatched(case_id)
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
    assert post("PAN", callback(dispatched(case_id)["PAN"])) == "applied"
    for number in range(1, 4):
        attempt = dispatched(case_id)["GST"]
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
    for kind, attempt in dispatched(case_id).items():
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
    with TestClient(app, base_url="http://localhost") as client:
        client.post("/api/demo/session", json={"persona": "merchant"})
        data = payload().model_dump()
        case = client.post("/api/applications", json=data)
        assert case.status_code == 201
        item = case.json()
        assert client.post("/api/applications", json=data).json()["id"] == item["id"]
        assert client.post("/api/applications", json={**data, "legal_name": "Other"}).status_code == 409
        path = "/api/applications/" + item["id"]
        source = path + "/documents/" + item["documents"][0]["id"]
        download = client.get(source)
        assert download.status_code == 200 and download.headers["content-disposition"].startswith(
            "attachment;"
        )
        assert "sandbox" in download.headers["content-security-policy"]
        assert client.get(path).headers["cache-control"] == "no-store"
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
        assert client.get(source + "/preview").status_code == 404
        assert (
            client.post(
                path + "/corrections", json={"expected_version": item["version"], "gstin": OLD_GST}
            ).status_code
            == 404
        )
        item_callback = callback(dispatched(item["id"])["PAN"])
        raw = item_callback.model_dump_json().encode()
        assert (
            client.post(
                "/api/webhooks/pan", content=raw, headers={"X-Webhook-Signature": "bad"}
            ).status_code
            == 401
        )
        assert dispatched(item["id"])["PAN"].status == "PENDING"
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
    assert post("GST", callback(dispatched(case_id)["GST"], False)) == "applied"
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


def test_only_matching_new_evidence_can_close_operator_request():
    case_id = application(payload(OLD_GST))
    post("GST", callback(dispatched(case_id)["GST"], False))
    with Session.begin() as db:
        case = wf.locked(db, case_id)
        wf.act(
            db,
            case,
            Action(
                action="request_info", kind="GST", expected_version=case.version, reason="Replace GST"
            ),
        )
        wf.correct(db, case, Correction(expected_version=case.version, gstin=NEW_GST))
        assert case.state == "NEEDS_INFO" and case.requested_kind == "GST"
        wf.correct(
            db, case, Correction(expected_version=case.version, documents=payload().documents[:1])
        )
        assert case.state == "NEEDS_INFO" and case.requested_kind == "GST"
        wf.correct(
            db, case, Correction(expected_version=case.version, documents=payload(OLD_GST).documents[1:])
        )
        assert case.requested_kind == "GST" and case.mid is None
        wf.correct(
            db, case, Correction(expected_version=case.version, documents=payload().documents[1:])
        )
        assert case.state == "VERIFYING" and case.requested_kind is case.requested_at is None


def test_replacing_one_unidentified_document_preserves_the_other():
    def binary(name):
        return Upload(
            filename=name,
            content_base64=base64.b64encode((FIXTURES / "gst-current.pdf").read_bytes()).decode(),
        )

    submission = payload()
    submission.documents = [binary("first.pdf"), binary("second.pdf")]
    case_id = application(submission)
    with Session.begin() as db:
        case = wf.locked(db, case_id)
        first, second = wf.unclassified(db, case)
        wf.correct(
            db,
            case,
            Correction(
                expected_version=case.version,
                documents=[binary("replacement.pdf")],
                replace_document_ids=[first.id],
            ),
        )
        assert first.status == "SUPERSEDED" and second.status == "PENDING"
        assert {d.filename for d in wf.unclassified(db, case)} == {"second.pdf", "replacement.pdf"}
        with pytest.raises(Problem, match="current unclassified"):
            wf.correct(
                db,
                case,
                Correction(
                    expected_version=case.version,
                    documents=[binary("another.pdf")],
                    replace_document_ids=[first.id],
                ),
            )
        assert len(wf.unclassified(db, case)) == 2


def test_callback_window_starts_once_at_dispatch(monkeypatch):
    case_id = application()
    with Session.begin() as db:
        case = wf.locked(db, case_id)
        attempt = wf.current(db, case)["PAN"]
        assert attempt.deadline is None
        assert not db.scalar(select(Job.id).where(Job.operation_key == "timeout/" + attempt.id))
        with pytest.raises(Problem, match="dispatched"):
            wf.receive(db, "PAN", callback(attempt))
        later = now() + timedelta(hours=8)
        monkeypatch.setattr(wf, "now", lambda: later)
        wf.expire(db, case, attempt)
        assert attempt.status == "PENDING" and attempt.number == 1
        assert wf.begin_dispatch(db, case, attempt)
        deadline = later + timedelta(seconds=wf.CHECK_TIMEOUT)
        assert attempt.deadline == deadline
        monkeypatch.setattr(wf, "now", lambda: later + timedelta(seconds=2))
        assert wf.begin_dispatch(db, case, attempt) and attempt.deadline == deadline
        assert db.scalar(select(Job.at).where(Job.operation_key == "timeout/" + attempt.id)) == deadline
        assert wf.receive(db, "PAN", callback(attempt)) == "applied"


def test_cross_case_event_id_is_reserved_atomically():
    events = [callback(dispatched(application())["PAN"]) for _ in range(2)]
    events[1].event_id = events[0].event_id
    start = Barrier(2)

    def deliver(event):
        start.wait(timeout=5)
        try:
            return post("PAN", event)
        except Problem as exc:
            return exc.status

    with ThreadPoolExecutor(2) as pool:
        assert set(pool.map(deliver, events)) == {"applied", 409}
    with Session() as db:
        assert sorted(db.get(Attempt, event.attempt_id).status for event in events) == [
            "PASSED",
            "PENDING",
        ]
        assert db.get(Receipt, ("PAN", events[0].event_id)).deliveries == 1


@pytest.mark.parametrize(
    "job_kind,reply",
    [
        ("dispatch", {}),
        ("dispatch", {"provider_ref": "other-attempt"}),
        ("issue", {"mid": "unspecified"}),
    ],
)
def test_invalid_success_response_uses_bounded_worker_retry(monkeypatch, job_kind, reply):
    case_id = application()
    if job_kind == "issue":
        for kind, attempt in dispatched(case_id).items():
            post(kind, callback(attempt))
    monkeypatch.setattr(
        worker.httpx,
        "post",
        lambda url, **kwargs: httpx.Response(200, json=reply, request=httpx.Request("POST", url)),
    )
    with Session() as db:
        job_id = db.scalar(
            select(Job.id).where(Job.case_id == case_id, Job.kind == job_kind).order_by(Job.id).limit(1)
        )
    for n in range(1, 4):
        with Session.begin() as db:
            db.get(Job, job_id).at = now() - timedelta(days=365)
        assert worker.run_once(documents_only=False)
        with Session() as db:
            job, case = db.get(Job, job_id), db.get(Case, case_id)
            assert job.tries == n and job.status == ("DEAD" if n == 3 else "READY")
            assert case.mid is None
    assert case.issuance == "TECHNICAL" if job_kind == "issue" else case.state == "IN_REVIEW"


def test_provider_delivery_identity_and_replay_during_acknowledgement(monkeypatch):
    body = VerifyInput(
        case_id=uid(), attempt_id=uid(), legal_name=LEGAL_NAME, pan=PAN, scenario="standard", number=1
    )
    assert simulator.verify("PAN", body) == simulator.verify("PAN", body)
    with pytest.raises(HTTPException) as conflict:
        simulator.verify("PAN", body.model_copy(update={"legal_name": "Second business"}))
    assert conflict.value.status_code == 409

    def due(tries=0):
        with Session.begin() as db:
            row = db.get(Delivery, body.attempt_id)
            row.due, row.tries = now() - timedelta(days=365), tries

    def result(status, replay=False):
        def send(url, **kwargs):
            if replay:
                simulator.replay(body.attempt_id)
            return httpx.Response(status, request=httpx.Request("POST", url))

        monkeypatch.setattr(simulator.httpx, "post", send)
        assert simulator.deliver_one()
        with Session() as db:
            return db.get(Delivery, body.attempt_id)

    due()
    assert result(422).failure == "HTTP 422"
    simulator.replay(body.attempt_id)
    due(5)
    assert result(503).failure == "HTTP 503"
    simulator.replay(body.attempt_id)
    due()
    row = result(200, replay=True)
    assert not row.delivered and row.failure is None and row.tries == 0
    due()
    assert result(200).delivered


def test_duplicate_registry_mid_cannot_stall_a_job_or_activate_a_second_case():
    case_ids = [application(), application()]
    mid = "MID-" + uid().replace("-", "")[:16].upper()
    for case_id in case_ids:
        for kind, check in dispatched(case_id).items():
            post(kind, callback(check))
        with Session.begin() as db:
            job = db.scalar(select(Job).where(Job.case_id == case_id, Job.kind == "issue"))
            job.status, job.lease_token, job.tries = "RUNNING", uid(), 3
            claimed = (job.id, case_id, job.lease_token)
        worker.finish(claimed, {"mid": mid}, None)
    with Session() as db:
        activated, blocked = (db.get(Case, case_id) for case_id in case_ids)
        assert activated.state == "ACTIVE" and activated.mid == mid
        assert blocked.state == "APPROVED" and blocked.mid is None and blocked.issuance == "TECHNICAL"
        assert db.get(Job, claimed[0]).status == "DEAD"
