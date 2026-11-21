import { useState } from "react"
import {
  Activity,
  AlertCircle,
  ArrowUpRight,
  BookOpenCheck,
  Braces,
  Check,
  CheckCheck,
  ChevronDown,
  CircleCheck,
  Clock3,
  ExternalLink,
  FileSearch,
  FileText,
  Fingerprint,
  History,
  Inbox,
  ListChecks,
  RefreshCw,
  ShieldCheck,
} from "lucide-react"
import { Breadcrumb } from "@/App"
import { Button } from "@/components/ui/button"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  api,
  dateTime,
  elapsed,
  go,
  message,
  stateNames,
  useResource,
  type CaseDetail,
  type Doc,
  type Kind,
  type Persona,
} from "@/lib/api"
import { ErrorNotice, Loading, Status } from "./common"

type Props = {
  id: string
  persona: Persona
  openAs: (role: "merchant" | "operator", path: string) => Promise<void>
}
const tabs = [
  { key: "evidence", label: "Evidence", icon: FileText },
  { key: "checks", label: "Verification", icon: ShieldCheck },
  { key: "activity", label: "Activity", icon: History },
]

function blockerInfo(c: CaseDetail) {
  if (c.request_note) return c.request_note
  if (c.reason === "GST registration inactive")
    return "The GSTIN matches the submitted evidence. The simulated registry reports that this registration is inactive."
  if (c.state === "ACTIVE")
    return "All required checks passed. The merchant registry recorded this application once."
  if (c.state === "VERIFYING")
    return "Provider requests are in flight. This case updates as signed callbacks arrive."
  if (c.state === "SUBMITTED")
    return "The document reader is examining the current evidence revision."
  if (c.state === "APPROVED")
    return "The approval decision is recorded. The merchant registry is processing issuance."
  if (c.state === "NEEDS_INFO")
    return "Update the indicated field or upload a replacement document."
  if (c.state === "IN_REVIEW")
    return "Review the source evidence and the current check results before taking action."
  return "The operator closed this application."
}

function ActionModal({
  name,
  c,
  onClose,
  onSaved,
}: {
  name: string
  c: CaseDetail
  onClose: () => void
  onSaved: () => void
}) {
  const preferred: Kind = c.reason.startsWith("PAN") ? "PAN" : "GST"
  const [kind, setKind] = useState<Kind>(preferred)
  const [reason, setReason] = useState(
    name === "request_info"
      ? "Please supply a current " +
          preferred +
          " document and update the corresponding details."
      : "",
  )
  const [version] = useState(c.version)
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false)
  const labels: Record<string, string> = {
    request_info: "Request current evidence",
    reject: "Reject application",
    retry: "Retry operation",
  }
  async function save(e: React.FormEvent) {
    e.preventDefault()
    setError("")
    setBusy(true)
    try {
      await api("/api/operator/cases/" + c.id + "/actions", {
        action: name,
        expected_version: version,
        kind: name === "request_info" ? kind : null,
        reason,
      })
      onSaved()
      onClose()
    } catch (err) {
      setError(message(err))
      setBusy(false)
    }
  }
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !busy) onClose()
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{labels[name]}</DialogTitle>
          <DialogDescription>
            {name === "request_info"
              ? "The merchant will see this request in their portal."
              : name === "reject"
                ? "This closes the case and records your reason in its history."
                : "The worker will retry the outstanding technical operation."}
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={save} className="form-stack">
          {name === "request_info" && (
            <label>
              Requested evidence
              <select
                value={kind}
                onChange={(e) => setKind(e.target.value as Kind)}
              >
                <option>PAN</option>
                <option>GST</option>
              </select>
            </label>
          )}
          <label>
            {name === "request_info" ? "Message to merchant" : "Reason"}
            <textarea
              required
              maxLength={500}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={3}
            />
          </label>
          {error && <ErrorNotice text={error} />}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button
              disabled={busy || !reason.trim()}
              variant={name === "reject" ? "destructive" : "default"}
            >
              {busy
                ? "Saving…"
                : name === "request_info"
                  ? "Send request"
                  : labels[name]}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function ChecksSummary({ c }: { c: CaseDetail }) {
  return (
    <div className="provider-list">
      {c.policy.map((kind) => {
        const a = c.attempts.find((x) => x.kind === kind && x.current)
        const Icon = kind === "PAN" ? Fingerprint : BookOpenCheck
        return (
          <div className="provider-row" key={kind}>
            <span className="small-icon">
              <Icon />
            </span>
            <div className="provider-text">
              <strong>
                {kind === "PAN" ? "PAN identity" : "GST registration"}
              </strong>
              <small>
                {a ? "Attempt " + a.number : "Awaiting valid evidence"}
              </small>
            </div>
            <Status value={a?.status || "PENDING"} />
          </div>
        )
      })}
    </div>
  )
}

function EvidencePanel({
  c,
  doc,
  choose,
}: {
  c: CaseDetail
  doc?: Doc
  choose: (id: string) => void
}) {
  const href = doc ? "/api/applications/" + c.id + "/documents/" + doc.id : ""
  const [full, setFull] = useState(false),
    [failedPreview, setFailedPreview] = useState("")
  const [page, setPage] = useState(1)
  const source = (
    <>
      {doc &&
        (doc.mime === "application/json" ? (
          <div className="structured">
            <div className="file-symbol">
              <Braces />
            </div>
            <h3>Structured {doc.kind} document</h3>
            {Object.entries(doc.fields).map(([k, v]) => (
              <div key={k}>
                <small>{k.replaceAll("_", " ")}</small>
                <strong>{v.value}</strong>
              </div>
            ))}
          </div>
        ) : failedPreview === doc.id ? (
          <div className="empty">
            <FileSearch />
            <p>Preview unavailable</p>
            <a href={href} target="_blank" rel="noreferrer">
              Open original document
            </a>
          </div>
        ) : (
          <img
            src={href + "/preview?page=" + page}
            alt={doc.kind + " evidence · " + doc.filename + " · page " + page}
            onError={() => setFailedPreview(doc.id)}
          />
        ))}
    </>
  )
  const rows = doc?.current ? c.fields.filter((f) => f.kind === doc.kind) : []
  return (
    <div className="evidence-grid">
      <section className="panel document-panel">
        <div className="panel-title">
          <div className="section-heading">
            <FileSearch />
            <h2>Source document</h2>
          </div>
          {doc && (
            <a
              className="icon-link"
              title="Open original document"
              aria-label="Open original document"
              href={href}
              target="_blank"
              rel="noreferrer"
            >
              <ExternalLink />
            </a>
          )}
        </div>
        {doc ? (
          <>
            <div className="file-picker">
              <FileText />
              <select
                aria-label="Choose evidence revision"
                value={doc.id}
                onChange={(e) => choose(e.target.value)}
              >
                {c.documents.map((d) => (
                  <option value={d.id} key={d.id}>
                    {d.filename} · v{d.revision}
                    {d.current ? "" : " · Previous"}
                  </option>
                ))}
              </select>
              <ChevronDown />
            </div>
            <div className="paper-well">
              <div className="paper-crop">{source}</div>
            </div>
            <div className="doc-foot">
              <span>
                {doc.method} · Revision {doc.revision}
              </span>
              <button className="text-button" onClick={() => setFull(true)}>
                View document
              </button>
            </div>
            {doc.note && (
              <p className="reader-note" role="status">
                {doc.note}
              </p>
            )}
            <Dialog open={full} onOpenChange={setFull}>
              <DialogContent className="source-dialog">
                <DialogHeader>
                  <DialogTitle>{doc.filename}</DialogTitle>
                  <DialogDescription>
                    {doc.method} · Revision {doc.revision}
                  </DialogDescription>
                </DialogHeader>
                <div className="full-source">{source}</div>
                <a
                  className="text-button"
                  href={href}
                  target="_blank"
                  rel="noreferrer"
                >
                  Open original file <ExternalLink size={14} />
                </a>
              </DialogContent>
            </Dialog>
          </>
        ) : (
          <div className="empty">
            <FileText />
            <p>No documents received</p>
          </div>
        )}
      </section>
      <div className="panel-stack">
        <section className="panel">
          <div className="panel-title">
            <div className="section-heading">
              <ListChecks />
              <h2>Field comparison</h2>
            </div>
          </div>
          {rows.length ? (
            rows.map((f) => (
              <div className="field-row" key={f.key}>
                <div className="field-title">
                  <span>{f.label}</span>
                  <Status value={f.status} />
                </div>
                <div className="values">
                  <div>
                    <small>Declared</small>
                    <span
                      className={
                        f.key === "gstin" || f.key === "pan" ? "mono" : ""
                      }
                    >
                      {f.declared || "Missing"}
                    </span>
                  </div>
                  <div>
                    <small>Extracted</small>
                    <span
                      className={
                        f.key === "gstin" || f.key === "pan" ? "mono" : ""
                      }
                    >
                      {f.extracted || "Unreadable"}
                    </span>
                  </div>
                </div>
                {f.source && (
                  <button
                    className="source-ref text-button"
                    aria-label={"View source for " + f.label}
                    onClick={() => {
                      setPage(f.page || 1)
                      setFull(true)
                    }}
                  >
                    <FileText size={12} />
                    {f.source}
                  </button>
                )}
                {f.quote && (
                  <details className="quote">
                    <summary>Source quote</summary>
                    <p>{f.quote}</p>
                  </details>
                )}
              </div>
            ))
          ) : (
            <p className="panel-copy">
              {doc && !doc.current
                ? "This is a previous evidence revision. Select the current revision to compare form values."
                : "Comparison appears after the reader identifies the document."}
            </p>
          )}
        </section>
        <section className="panel">
          <div className="panel-title">
            <div className="section-heading">
              <ShieldCheck />
              <h2>Provider checks</h2>
            </div>
          </div>
          <ChecksSummary c={c} />
        </section>
      </div>
    </div>
  )
}

function Verification({
  c,
  replay,
}: {
  c: CaseDetail
  replay: () => Promise<void>
}) {
  const [busy, setBusy] = useState(false)
  return (
    <div className="evidence-grid">
      <section className="panel">
        <div className="panel-title">
          <div className="section-heading">
            <ShieldCheck />
            <h2>Verification attempts</h2>
          </div>
        </div>
        {c.attempts.length ? (
          [...c.attempts].reverse().map((a) => (
            <div key={a.id} className="attempt">
              <div>
                <strong>
                  {a.kind} · Attempt {a.number}
                </strong>
                <small>
                  {a.current ? "Current inputs" : "Earlier inputs or attempt"} ·{" "}
                  {dateTime(a.created_at)}
                </small>
                <p>{a.reason}</p>
              </div>
              <Status value={a.status} />
            </div>
          ))
        ) : (
          <p className="panel-copy">
            Requests start when the required fields validate.
          </p>
        )}
      </section>
      <section className="panel">
        <div className="panel-title">
          <div className="section-heading">
            <Inbox />
            <h2>Webhook receipts</h2>
          </div>
        </div>
        {c.receipts.length ? (
          <>
            {[...c.receipts].reverse().map((r) => (
              <div className="receipt" key={r.event_id}>
                <div>
                  <strong>{r.kind} callback</strong>
                  <small>
                    {r.disposition} · {r.deliveries}{" "}
                    {r.deliveries === 1 ? "delivery" : "deliveries"}
                  </small>
                </div>
                <span className="mono">{r.event_id.slice(0, 8)}</span>
              </div>
            ))}
            <div className="panel-copy">
              <Button
                disabled={busy}
                variant="outline"
                onClick={async () => {
                  setBusy(true)
                  try {
                    await replay()
                  } finally {
                    setBusy(false)
                  }
                }}
              >
                <RefreshCw />
                Replay latest callback
              </Button>
            </div>
          </>
        ) : (
          <p className="panel-copy">
            Signed provider deliveries appear here in the operator workspace.
          </p>
        )}
      </section>
    </div>
  )
}

export function CasePage({ id, persona, openAs }: Props) {
  const {
    data: c,
    error,
    reload,
  } = useResource<CaseDetail>("/api/applications/" + id, true)
  const [tab, setTab] = useState("evidence"),
    [selected, setSelected] = useState(""),
    [action, setAction] = useState("")
  const [notice, setNotice] = useState(""),
    [actionError, setActionError] = useState("")
  if (!c)
    return error ? <ErrorNotice text={error} retry={reload} /> : <Loading />
  const operator = persona.role === "operator",
    done = c.state === "ACTIVE",
    attention = ["IN_REVIEW", "NEEDS_INFO", "REJECTED"].includes(c.state)
  const doc =
    c.documents.find((d) => d.id === selected) ||
    c.documents.find((d) => d.current && d.kind === "GST") ||
    c.documents.find((d) => d.current)
  const Icon = done ? CircleCheck : attention ? AlertCircle : Clock3
  return (
    <>
      <Breadcrumb operator={operator} label={c.reference} />
      <div className="page-heading">
        <div>
          <div className="title-inline">
            <h1>{c.legal_name}</h1>
            <Status value={c.state} />
          </div>
          <div className="case-meta">
            <span>Submitted {dateTime(c.created_at)}</span>
            <span>
              Next actor <b>{c.next_actor}</b>
            </span>
            {c.mid && <span className="mono">{c.mid}</span>}
          </div>
        </div>
        {operator && (
          <Button
            variant="outline"
            onClick={() => openAs("merchant", "/case/" + id)}
          >
            Merchant view <ArrowUpRight />
          </Button>
        )}
        {!operator && (
          <Button
            variant="outline"
            onClick={() => openAs("operator", "/case/" + id)}
          >
            Operator view <ArrowUpRight />
          </Button>
        )}
      </div>
      {(error || actionError) && (
        <ErrorNotice
          text={actionError || error || ""}
          retry={() => {
            setActionError("")
            reload()
          }}
        />
      )}
      <Alert
        className={
          "decision-alert " +
          (done ? "success" : attention ? "warning" : "neutral")
        }
        role="status"
      >
        <Icon />
        <div>
          <AlertTitle>{c.reason}</AlertTitle>
          <AlertDescription>{blockerInfo(c)}</AlertDescription>
        </div>
        {operator &&
          c.actions.includes("request_info") &&
          !c.actions.includes("retry") && (
            <Button onClick={() => setAction("request_info")}>
              {c.reason.includes("GST")
                ? "Request current certificate"
                : "Request evidence"}
            </Button>
          )}
        {operator && c.actions.includes("retry") && (
          <Button onClick={() => setAction("retry")}>
            <RefreshCw />
            Retry operation
          </Button>
        )}
        {!operator && c.can_correct && (
          <Button onClick={() => go("/correct/" + id)}>
            {c.state === "NEEDS_INFO"
              ? "Submit correction"
              : "Update application"}
          </Button>
        )}
        {operator && c.state === "NEEDS_INFO" && (
          <Button onClick={() => openAs("merchant", "/correct/" + id)}>
            Open merchant correction
          </Button>
        )}
      </Alert>
      <div className="case-toolbar">
        <nav aria-label="Case sections" className="case-tabs">
          {tabs.map((t) => (
            <button
              aria-pressed={t.key === tab}
              key={t.key}
              onClick={() => {
                setTab(t.key)
                setNotice("")
              }}
            >
              <t.icon />
              {t.label}
            </button>
          ))}
        </nav>
        <span className="state-age">
          <Clock3 />
          {elapsed(c.state_since)} in state
        </span>
      </div>
      {notice && (
        <div className="notice" role="status">
          <Check />
          {notice}
        </div>
      )}
      {tab === "evidence" && (
        <EvidencePanel key={doc?.id} c={c} doc={doc} choose={setSelected} />
      )}
      {tab === "checks" && (
        <Verification
          c={c}
          replay={async () => {
            try {
              await api("/api/operator/cases/" + id + "/replay", {})
              setNotice(
                "Replay queued. The receipt count updates when the callback arrives.",
              )
              reload()
            } catch (e) {
              setActionError(message(e))
            }
          }}
        />
      )}
      {tab === "activity" && (
        <section className="panel">
          <div className="panel-title">
            <div className="section-heading">
              <Activity />
              <h2>Case activity</h2>
            </div>
            <span className="secondary">{c.events.length} events</span>
          </div>
          <ol className="timeline">
            {[...c.events].reverse().map((e) => (
              <li key={e.id}>
                <span className="event-dot">
                  <CheckCheck />
                </span>
                <div>
                  <strong>
                    {e.title.includes(" → ")
                      ? e.title
                          .split(" → ")
                          .map((s) => stateNames[s] || s)
                          .join(" → ")
                      : e.title}
                  </strong>
                  <p>{e.detail}</p>
                  <small>
                    {e.actor} · {dateTime(e.at)}
                  </small>
                </div>
              </li>
            ))}
          </ol>
        </section>
      )}
      {operator && c.actions.includes("reject") && (
        <div className="secondary-actions">
          {c.actions.includes("retry") && (
            <button
              className="text-button"
              onClick={() => setAction("request_info")}
            >
              Request replacement evidence
            </button>
          )}
          <button className="text-button" onClick={() => setAction("reject")}>
            Reject application
          </button>
        </div>
      )}
      {action && (
        <ActionModal
          name={action}
          c={c}
          onClose={() => setAction("")}
          onSaved={() => {
            setNotice("Action recorded in case activity.")
            reload()
          }}
        />
      )}
    </>
  )
}
