import { useEffect, useState } from "react"
import {
  Building2,
  ChevronRight,
  Inbox,
  Layers3,
  UserRound,
} from "lucide-react"
import {
  api,
  go,
  message,
  useResource,
  useRoute,
  type Persona,
  type Summary,
} from "@/lib/api"
import { ErrorNotice, Loading } from "@/components/common"
import { CasePage } from "@/components/CasePage"
import { IntakePage } from "@/components/IntakePage"
import { QueuePage } from "@/components/QueuePage"

function FirstCase({ persona }: { persona: Persona }) {
  const resource = useResource<Summary[]>(
    persona.role === "operator" ? "/api/operator/cases" : "/api/applications",
  )
  useEffect(() => {
    if (!resource.data) return
    const next =
      resource.data.find((c) => c.state === "IN_REVIEW") || resource.data[0]
    go(persona.role === "operator" && next ? "/case/" + next.id : "/queue")
  }, [resource.data, persona.role])
  return resource.error ? (
    <ErrorNotice text={resource.error} retry={resource.reload} />
  ) : (
    <Loading label="Opening workspace" />
  )
}

export default function App() {
  const route = useRoute()
  const [persona, setPersona] = useState<Persona>()
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    api<Persona>("/api/session")
      .then(setPersona)
      .catch((e) => setError(message(e)))
  }, [])
  async function openAs(role: "merchant" | "operator", path: string) {
    setBusy(true)
    setError("")
    try {
      if (persona?.role !== role)
        setPersona(await api<Persona>("/api/demo/session", { persona: role }))
      go(path)
    } catch (e) {
      setError(message(e))
    } finally {
      setBusy(false)
    }
  }
  const [, page, id] = route.split("/")
  const operator = persona?.role === "operator"
  return (
    <div className="app-frame">
      <header className="masthead">
        <div className="mast-inner">
          <a className="brand" href="#/">
            <span className="brand-icon">
              <Layers3 />
            </span>
            Merchant desk
          </a>
          <nav aria-label="Workspace" className="workspace-nav">
            <button
              disabled={busy}
              className={operator ? "selected" : ""}
              onClick={() => openAs("operator", "/queue")}
            >
              <Inbox />
              Review queue
            </button>
            <button
              disabled={busy}
              className={persona && !operator ? "selected" : ""}
              onClick={() => openAs("merchant", "/queue")}
            >
              <Building2 />
              Merchant portal
            </button>
          </nav>
          <div className="persona">
            <span className="avatar">
              <UserRound />
            </span>
            <span>
              {operator
                ? "Demo operator"
                : persona
                  ? "Demo merchant"
                  : "Demo workspace"}
            </span>
          </div>
        </div>
      </header>
      <main className="page-width">
        {error && <ErrorNotice text={error} retry={() => location.reload()} />}
        {!persona ? (
          <Loading label="Opening workspace" />
        ) : (
          <div key={persona.role}>
            {!page ? (
              <FirstCase persona={persona} />
            ) : page === "case" && id ? (
              <CasePage key={id} id={id} persona={persona} openAs={openAs} />
            ) : page === "correct" && id && !operator ? (
              <IntakePage key={id} caseId={id} />
            ) : page === "apply" && !operator ? (
              <IntakePage />
            ) : (
              <QueuePage
                operator={operator}
                newApplication={() => openAs("merchant", "/apply")}
              />
            )}
          </div>
        )}
      </main>
      <footer className="page-width footer">
        <span>
          <Layers3 /> Merchant desk
        </span>
        <span>
          Local demo <span aria-hidden="true">·</span> Synthetic merchant data
        </span>
      </footer>
    </div>
  )
}

export function Breadcrumb({
  operator = true,
  label,
}: {
  operator?: boolean
  label: string
}) {
  return (
    <div className="breadcrumb">
      <a href="#/queue">{operator ? "Review queue" : "My applications"}</a>
      <ChevronRight />
      <span>{label}</span>
    </div>
  )
}
