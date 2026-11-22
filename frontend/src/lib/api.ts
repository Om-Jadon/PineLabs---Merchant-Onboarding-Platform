import { useEffect, useState } from "react"

export type Kind = "PAN" | "GST"
export interface Persona {
  role: "merchant" | "operator"
  name: string
  owner: string
}
export interface Summary {
  id: string
  reference: string
  legal_name: string
  state: string
  reason: string
  next_actor: string
  version: number
  created_at: string
  state_since: string
  mid: string | null
}
export interface Doc {
  id: string
  kind: string
  revision: number
  filename: string
  mime: string
  method: string
  status: string
  current: boolean
  detected_kind: string | null
  note: string | null
  fields: Record<
    string,
    { value: string; source: string; page?: number; quote?: string }
  >
}
export interface Field {
  kind: string
  key: string
  label: string
  declared: string
  extracted: string
  status: string
  source: string | null
  page: number | null
  quote: string | null
}
export interface Attempt {
  id: string
  kind: string
  number: number
  status: string
  reason: string
  current: boolean
  deadline: string | null
  created_at: string
  provider_ref: string | null
}
export interface AuditEvent {
  id: number
  at: string
  actor: string
  title: string
  detail: string
  source_id: string | null
}
export interface Receipt {
  event_id: string
  kind: string
  attempt_id: string
  received_at: string
  disposition: string
  deliveries: number
}
export interface Declared {
  legal_name: string
  pan: string
  gstin: string
}
export interface CaseDetail extends Summary {
  declared: Declared
  policy: string[]
  issuance: string
  requested_kind: Kind | null
  request_note: string | null
  scenario: string
  actions: string[]
  can_correct: boolean
  documents: Doc[]
  fields: Field[]
  attempts: Attempt[]
  events: AuditEvent[]
  receipts: Receipt[]
}
export interface Samples {
  legal_name: string
  pan: string
  old_gstin: string
  current_gstin: string
  image_samples: Record<"clean" | "review", { declared: Declared; files: string[] }>
  files: string[]
}
export interface Upload {
  kind: Kind | "AUTO"
  filename: string
  fields?: Record<string, string>
  content_base64?: string
}

export class ApiError extends Error {
  constructor(public status: number, detail: string) {
    super(detail)
  }
}

export async function api<T>(
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    signal,
    cache: "no-store",
    ...(body === undefined
      ? {}
      : {
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }),
  })
  const value = await response.json().catch(() => null)
  if (!response.ok)
    throw new ApiError(
      response.status,
      typeof value?.detail === "string" ? value.detail : "The service could not complete this request.",
    )
  return value as T
}

export function useResource<T>(path: string, poll = false) {
  const [state, setState] = useState<{
    path: string
    data?: T
    error?: string
  }>({ path })
  const [generation, invalidate] = useState(0)
  useEffect(() => {
    const abort = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    async function load() {
      let refresh = poll
      try {
        const data = await api<T>(path, undefined, abort.signal)
        if (!abort.signal.aborted) setState({ path, data })
      } catch (err) {
        if (!abort.signal.aborted) {
          const lostAccess = err instanceof ApiError && [401, 403, 404].includes(err.status)
          if (lostAccess) refresh = false
          setState((old) => ({
            ...(old.path === path && !lostAccess ? old : {}),
            path,
            error: message(err),
          }))
        }
      } finally {
        if (refresh && !abort.signal.aborted) timer = setTimeout(load, 1500)
      }
    }
    void load()
    return () => {
      abort.abort()
      clearTimeout(timer)
    }
  }, [path, poll, generation])
  return {
    ...(state.path === path ? state : { path }),
    reload: () => invalidate((n) => n + 1),
  }
}

export const message = (err: unknown) =>
  err instanceof Error ? err.message : "Something went wrong. Try again."
export function go(path: string) {
  window.location.hash = path
}
export function useRoute() {
  const read = () => location.hash.slice(1) || "/"
  const [route, setRoute] = useState(read)
  useEffect(() => {
    const onHash = () => setRoute(read())
    addEventListener("hashchange", onHash)
    return () => removeEventListener("hashchange", onHash)
  }, [])
  return route
}
export const stateNames: Record<string, string> = {
  SUBMITTED: "Reading evidence",
  VERIFYING: "Verifying",
  IN_REVIEW: "Needs review",
  NEEDS_INFO: "Needs information",
  APPROVED: "Approved",
  ACTIVE: "Active",
  REJECTED: "Rejected",
}
export function dateTime(value: string) {
  return new Intl.DateTimeFormat("en-IN", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: true,
  }).format(new Date(value))
}
export function elapsed(value: string) {
  const seconds = Math.max(
    0,
    Math.floor((Date.now() - Date.parse(value)) / 1000),
  )
  if (seconds < 60) return "Under a minute"
  if (seconds < 3600) return Math.floor(seconds / 60) + " min"
  return (
    Math.floor(seconds / 3600) + "h " + Math.floor((seconds % 3600) / 60) + "m"
  )
}
