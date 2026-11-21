import os
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    UniqueConstraint,
    create_engine,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .settings import now


def uid():
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


def instant():
    return mapped_column(DateTime(timezone=True), default=now)


class Case(Base):
    __tablename__ = "cases"
    __table_args__ = (UniqueConstraint("owner", "submission_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    owner: Mapped[str]
    submission_key: Mapped[str]
    payload_hash: Mapped[str]
    legal_name: Mapped[str]
    pan: Mapped[str]
    gstin: Mapped[str]
    policy: Mapped[list] = mapped_column(JSON)
    scenario: Mapped[str]
    state: Mapped[str] = mapped_column(default="SUBMITTED", index=True)
    reason: Mapped[str] = mapped_column(default="Reading submitted evidence")
    requested_kind: Mapped[str | None]
    request_note: Mapped[str | None]
    issuance: Mapped[str] = mapped_column(default="NOT_STARTED")
    mid: Mapped[str | None] = mapped_column(unique=True)
    version: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = instant()
    state_since: Mapped[datetime] = instant()


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("case_id", "kind", "revision"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    kind: Mapped[str]
    revision: Mapped[int]
    filename: Mapped[str]
    mime: Mapped[str]
    data: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    method: Mapped[str]
    status: Mapped[str]
    detected_kind: Mapped[str | None]
    reader_note: Mapped[str | None]
    fields: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = instant()


class Attempt(Base):
    __tablename__ = "attempts"
    __table_args__ = (
        UniqueConstraint("case_id", "kind", "number"),
        Index(
            "one_current_attempt", "case_id", "kind", unique=True, postgresql_where=text("is_current")
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    kind: Mapped[str]
    number: Mapped[int]
    retry: Mapped[int] = mapped_column(default=0)
    fingerprint: Mapped[str]
    is_current: Mapped[bool] = mapped_column(default=True)
    status: Mapped[str] = mapped_column(default="PENDING")
    reason: Mapped[str] = mapped_column(default="Waiting for provider callback")
    provider_ref: Mapped[str | None]
    created_at: Mapped[datetime] = instant()
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    at: Mapped[datetime] = instant()
    actor: Mapped[str]
    title: Mapped[str]
    detail: Mapped[str]
    source_id: Mapped[str | None]


class Receipt(Base):
    __tablename__ = "receipts"
    provider: Mapped[str] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(primary_key=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("attempts.id"))
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    received_at: Mapped[datetime] = instant()
    disposition: Mapped[str]
    digest: Mapped[str]
    deliveries: Mapped[int] = mapped_column(default=1)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    kind: Mapped[str]
    source: Mapped[str]
    operation_key: Mapped[str] = mapped_column(unique=True)
    status: Mapped[str] = mapped_column(default="READY", index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    tries: Mapped[int] = mapped_column(default=0)
    lease_token: Mapped[str | None]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None]


class Delivery(Base):
    __tablename__ = "deliveries"
    __table_args__ = {"schema": "sim"}
    attempt_id: Mapped[str] = mapped_column(primary_key=True)
    provider: Mapped[str]
    payload: Mapped[dict] = mapped_column(JSON)
    due: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    delivered: Mapped[bool] = mapped_column(default=False)
    tries: Mapped[int] = mapped_column(default=0)


class Merchant(Base):
    __tablename__ = "merchants"
    __table_args__ = {"schema": "sim"}
    case_id: Mapped[str] = mapped_column(primary_key=True)
    mid: Mapped[str] = mapped_column(unique=True)


engine = create_engine(
    os.getenv("DATABASE_URL", "postgresql+psycopg://merchant:demo-local@localhost/merchant"),
    pool_pre_ping=True,
    hide_parameters=True,
)
Session = sessionmaker(engine, expire_on_commit=False)
