import { useState } from "react"
import {
  Download,
  FileCheck2,
  Files,
  FileText,
  UploadCloud,
  X,
} from "lucide-react"
import { Breadcrumb } from "@/App"
import { Button } from "./ui/button"
import { ErrorNotice, Loading } from "./common"
import {
  api,
  ApiError,
  go,
  message,
  useResource,
  type CaseDetail,
  type Declared,
  type Samples,
  type Upload,
} from "@/lib/api"

const empty: Declared = { legal_name: "", pan: "", gstin: "" }

async function readUpload(file: File): Promise<Upload> {
  if (file.size > 3 * 1024 * 1024)
    throw new Error("Each document must fit within 3 MiB.")
  if (/\.json$/i.test(file.name)) {
    let fields
    try {
      fields = JSON.parse(await file.text())
    } catch {
      throw new Error("The JSON file could not be read.")
    }
    if (
      !fields ||
      Array.isArray(fields) ||
      typeof fields !== "object" ||
      !Object.values(fields).every((v) => typeof v === "string")
    ) {
      throw new Error(
        "A structured document needs a JSON object containing text fields.",
      )
    }
    const pan = "pan" in fields || "holder_name" in fields
    const gst = "gstin" in fields || "legal_name" in fields
    if (pan === gst)
      throw new Error(
        "Supply separate PAN and GST JSON documents with identifiable fields.",
      )
    return { kind: pan ? "PAN" : "GST", filename: file.name, fields }
  }
  const encoded = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result).split(",")[1])
    reader.onerror = () =>
      reject(new Error("The document could not be opened."))
    reader.readAsDataURL(file)
  })
  return { kind: "AUTO", filename: file.name, content_base64: encoded }
}

async function sampleFile(name: string) {
  const response = await fetch("/api/demo/files/" + name)
  if (!response.ok) throw new Error("The sample document could not be loaded.")
  return readUpload(new File([await response.blob()], name))
}

function Form({ initial, reload }: { initial?: CaseDetail; reload?: () => void }) {
  const [fields, setFields] = useState<Declared>(initial?.declared || empty)
  const [version] = useState(initial?.version)
  const [files, setFiles] = useState<Upload[]>([])
  const [replaced, setReplaced] = useState<string[]>([])
  const [stale, setStale] = useState(false)
  const [key] = useState(() => crypto.randomUUID())
  const [scenario, setScenario] = useState("standard")
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("")
  const { data: samples } = useResource<Samples>("/api/demo/fixtures")
  const correcting = Boolean(initial)
  const needsPan = initial?.requested_kind === "PAN"
  const unclassified = initial?.documents.filter((d) => d.current && d.kind === "AUTO") || []
  async function loadSample(active: boolean) {
    if (!samples) return
    setBusy(true)
    setError("")
    try {
      const names = correcting
        ? [needsPan ? "pan.json" : "gst-current.pdf"]
        : ["pan.json", active ? "gst-current.pdf" : "gst-inactive.pdf"]
      const uploads = await Promise.all(names.map(sampleFile))
      setFiles(uploads)
      setReplaced([])
      setFields((current) =>
        correcting
          ? { ...current, ...(needsPan ? { pan: samples.pan } : { gstin: samples.current_gstin }) }
          : {
              legal_name: samples.legal_name,
              pan: samples.pan,
              gstin: active ? samples.current_gstin : samples.old_gstin,
            },
      )
    } catch (e) {
      setError(message(e))
    } finally {
      setBusy(false)
    }
  }
  async function addFiles(added: File[]) {
    setError("")
    setBusy(true)
    try {
      if (added.length + files.length > 2)
        throw new Error(
          "Attach up to two documents. Remove a selected file to replace it.",
        )
      const uploads = await Promise.all(added.map(readUpload))
      setFiles((current) => [...current, ...uploads])
    } catch (e) {
      setError(message(e))
    } finally {
      setBusy(false)
    }
  }
  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError("")
    try {
      const body = {
        ...fields,
        documents: files,
        ...(initial
          ? { expected_version: version, replace_document_ids: replaced }
          : { submission_key: key, scenario }),
      }
      const target = initial
        ? "/api/applications/" + initial.id + "/corrections"
        : "/api/applications"
      const result = await api<CaseDetail>(target, body)
      go("/case/" + result.id)
    } catch (e) {
      setError(message(e))
      setStale(Boolean(initial) && e instanceof ApiError && e.status === 409)
      setBusy(false)
    }
  }
  if (initial && !initial.can_correct)
    return (
      <>
        <Breadcrumb operator={false} label={initial.reference} />
        <div className="panel panel-copy">
          This case is closed for edits.{" "}
          <a className="text-button" href={"#/case/" + initial.id}>
            View application
          </a>
        </div>
      </>
    )
  return (
    <>
      <Breadcrumb
        operator={false}
        label={initial?.reference || "New application"}
      />
      <div className="page-heading">
        <div>
          <h1>{correcting ? "Correct application" : "New application"}</h1>
          <p className="subtext">
            {initial?.request_note ||
              (correcting
                ? "Update the details or evidence that need another check."
                : "Enter the business details and attach PAN and GST evidence.")}
          </p>
        </div>
      </div>
      <div className="intake-grid">
        <form onSubmit={submit} className="panel intake-form form-stack">
          <div className="section-heading">
            <FileText />
            <h2>Business details</h2>
          </div>
          <label>
            Legal business name
            <input
              maxLength={140}
              value={fields.legal_name}
              onChange={(e) =>
                setFields({ ...fields, legal_name: e.target.value })
              }
            />
          </label>
          <div className="form-pair">
            {(["pan", "gstin"] as const).map((k) => (
              <label key={k}>
                {k.toUpperCase()}
                <input
                  autoCapitalize="characters"
                  maxLength={k === "pan" ? 24 : 28}
                  value={fields[k]}
                  onChange={(e) =>
                    setFields({ ...fields, [k]: e.target.value.toUpperCase() })
                  }
                />
              </label>
            ))}
          </div>
          <div className="upload-heading">
            <h2>{correcting ? "Replacement evidence" : "Evidence"}</h2>
            <span>Up to two files</span>
          </div>
          {initial && (
            <p className="helper">
              Existing evidence stays attached. Add a file to supply a new
              revision.
            </p>
          )}
          <label className="upload-zone">
            <UploadCloud />
            <strong>Choose documents</strong>
            <span>PDF, PNG, JPEG, WebP or JSON · 3 MiB each</span>
            <input
              aria-label="Choose documents"
              type="file"
              multiple
              disabled={busy}
              accept=".json,.pdf,.png,.jpeg,.jpg,.webp"
              onChange={(e) => {
                const added = Array.from(e.target.files || [])
                e.target.value = ""
                void addFiles(added)
              }}
            />
          </label>
          {files.length > 0 && (
            <ul className="upload-list">
              {files.map((f, i) => (
                <li key={i}>
                  <FileCheck2 />
                  <span>{f.filename}</span>
                  <button
                    type="button"
                    aria-label={"Remove " + f.filename}
                    disabled={busy}
                    onClick={() => {
                      setFiles(files.filter((_, j) => i !== j))
                      setReplaced([])
                    }}
                  >
                    <X />
                  </button>
                </li>
              ))}
            </ul>
          )}
          {files.length > 0 && unclassified.length > 0 && (
            <fieldset className="replacement-list">
              <legend>Which unidentified files do these uploads replace?</legend>
              {unclassified.map((doc) => (
                <label key={doc.id}>
                  <input
                    type="checkbox"
                    checked={replaced.includes(doc.id)}
                    disabled={busy || (!replaced.includes(doc.id) && replaced.length >= files.length)}
                    onChange={(e) => setReplaced(e.target.checked
                      ? [...replaced, doc.id]
                      : replaced.filter((id) => id !== doc.id))}
                  />
                  {doc.filename} · v{doc.revision}
                </label>
              ))}
            </fieldset>
          )}
          <p className="helper">
            Use synthetic documents. When Gemini is configured, the worker sends
            the attached PDF or image to Google for extraction.
          </p>
          {error && <ErrorNotice text={error} />}
          {stale && (
            <Button type="button" variant="outline" onClick={() => {
              setReplaced([])
              setStale(false)
              reload?.()
            }}>
              Reload current application
            </Button>
          )}
          <div className="form-actions">
            <a href={initial ? "#/case/" + initial.id : "#/queue"}>Cancel</a>
            <Button type="submit" disabled={busy || stale}>
              {busy
                ? "Working…"
                : correcting
                  ? "Submit correction"
                  : "Submit application"}
            </Button>
          </div>
        </form>
        <aside className="panel sample-panel">
          <div className="section-heading">
            <Files />
            <h2>Demo samples</h2>
          </div>
          <p>
            {correcting
              ? needsPan
                ? "The PAN record contains the holder name and PAN for Riverbend Stationery."
                : "The current certificate carries the active GSTIN for Riverbend Stationery."
              : "Try Riverbend Stationery with an inactive or current GST registration."}
          </p>
          {correcting ? (
            <Button
              type="button"
              variant="outline"
              disabled={busy || !samples}
              onClick={() => loadSample(true)}
            >
              {needsPan ? "Load PAN record" : "Load current certificate"}
            </Button>
          ) : (
            <>
              <Button
                type="button"
                variant="outline"
                disabled={busy || !samples}
                onClick={() => loadSample(false)}
              >
                Load review sample
              </Button>
              <Button
                type="button"
                variant="outline"
                disabled={busy || !samples}
                onClick={() => loadSample(true)}
              >
                Load clean sample
              </Button>
            </>
          )}
          {!correcting && (
            <label className="scenario">
              Provider simulation
              <select
                value={scenario}
                onChange={(e) => setScenario(e.target.value)}
              >
                <option value="standard">Standard delivery</option>
                <option value="late_callback">
                  First GST callback arrives late
                </option>
                <option value="lost_mid_response">
                  First MID response is lost
                </option>
              </select>
            </label>
          )}
          {samples && (
            <details className="sample-files">
              <summary>Download test documents</summary>
              {samples.files.map((f) => (
                <a key={f} href={"/api/demo/files/" + f} download>
                  <Download size={14} />
                  {f}
                </a>
              ))}
            </details>
          )}
        </aside>
      </div>
    </>
  )
}

function Existing({ id }: { id: string }) {
  const { data, error, reload } = useResource<CaseDetail>(
    "/api/applications/" + id,
  )
  return data ? (
    <Form key={data.id + "/" + data.version} initial={data} reload={reload} />
  ) : error ? (
    <ErrorNotice text={error} retry={reload} />
  ) : (
    <Loading />
  )
}

export function IntakePage({ caseId }: { caseId?: string }) {
  return caseId ? <Existing id={caseId} /> : <Form />
}
