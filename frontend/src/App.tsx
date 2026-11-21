import { useEffect, useRef, useState } from "react"
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
  const sessionGeneration = useRef(0)
  const switching = useRef(false)
  useEffect(() => {
    async function readSession() {
      const generation = ++sessionGeneration.current
      try {
        const current = await api<Persona>("/api/session")
        if (generation === sessionGeneration.current) {
          setPersona(current)
          setError("")
        }
      } catch (e) {
        if (generation === sessionGeneration.current) setError(message(e))
      }
    }
    function onSessionChange(event: StorageEvent) {
      if (event.key !== "merchant-desk-session") return
      setPersona(undefined)
      go("/queue")
      void readSession()
    }
    function onFocus() {
      if (!switching.current) void readSession()
    }
    void readSession()
    addEventListener("storage", onSessionChange)
    addEventListener("focus", onFocus)
    return () => {
      sessionGeneration.current++
      removeEventListener("storage", onSessionChange)
      removeEventListener("focus", onFocus)
    }
  }, [])
  async function openAs(role: "merchant" | "operator", path: string) {
    setBusy(true)
    switching.current = true
    setError("")
    try {
      if (persona?.role !== role) {
        const generation = ++sessionGeneration.current
        const current = await api<Persona>("/api/demo/session", { persona: role })
        try {
          localStorage.setItem("merchant-desk-session", crypto.randomUUID())
        } catch {
          // Browsers with storage disabled still refresh their session when a tab regains focus.
        }
        if (generation !== sessionGeneration.current) return
        setPersona(current)
      }
      go(path)
    } catch (e) {
      setError(message(e))
    } finally {
      switching.current = false
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
              disabled={busy || !persona}
              className={operator ? "selected" : ""}
              onClick={() => openAs("operator", "/queue")}
            >
              <Inbox />
              Review queue
            </button>
            <button
              disabled={busy || !persona}
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
          <div key={persona.role + "/" + persona.owner}>
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
