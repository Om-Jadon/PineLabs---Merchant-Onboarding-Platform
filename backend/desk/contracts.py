from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Kind = Literal["PAN", "GST"]
Scenario = Literal["standard", "late_callback", "lost_mid_response"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Upload(Contract):
    kind: Literal["PAN", "GST", "AUTO"] = "AUTO"
    filename: str = Field(min_length=1, max_length=120)
    fields: dict[str, str] | None = None
    content_base64: str | None = Field(None, max_length=4_194_304)

    @model_validator(mode="after")
    def one_source(self):
        if (self.fields is None) == (self.content_base64 is None):
            raise ValueError("Supply one structured payload or one file.")
        if self.fields is not None and self.kind == "AUTO":
            raise ValueError("Specify PAN or GST for a structured document.")
        if self.fields is not None and (
            len(self.fields) > 16 or any(len(k) > 80 or len(v) > 500 for k, v in self.fields.items())
        ):
            raise ValueError("Structured document exceeds its field limits.")
        return self


class DocumentsInput(Contract):
    documents: list[Upload] = Field(default_factory=list, max_length=2)

    @model_validator(mode="after")
    def distinct_kinds(self):
        typed = [d.kind for d in self.documents if d.kind != "AUTO"]
        if len(set(typed)) != len(typed):
            raise ValueError("Supply a single revision for each document type.")
        return self


class Submission(DocumentsInput):
    submission_key: str = Field(min_length=1, max_length=100)
    legal_name: str = Field(default="", max_length=140)
    pan: str = Field(default="", max_length=24)
    gstin: str = Field(default="", max_length=28)
    scenario: Scenario = "standard"


class Correction(DocumentsInput):
    expected_version: int = Field(ge=1)
    legal_name: str | None = Field(None, max_length=140)
    pan: str | None = Field(None, max_length=24)
    gstin: str | None = Field(None, max_length=28)


class Action(Contract):
    action: Literal["request_info", "retry", "reject"]
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500)
    kind: Kind | None = None


class Callback(Contract):
    event_id: str = Field(min_length=1, max_length=100)
    attempt_id: str = Field(min_length=1, max_length=64)
    provider_ref: str = Field(min_length=1, max_length=100)
    outcome: Literal["PASSED", "FAILED"]
    reason: Literal["PAN_MATCH", "GST_ACTIVE", "GST_INACTIVE", "RECORD_MISMATCH"]


class VerifyInput(Contract):
    attempt_id: str
    case_id: str
    legal_name: str
    pan: str
    gstin: str = ""
    scenario: Scenario
    number: int


class IssueInput(Contract):
    case_id: str
    scenario: Scenario
