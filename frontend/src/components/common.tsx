import {
  AlertCircle,
  Circle,
  CircleCheck,
  Clock3,
  LoaderCircle,
} from "lucide-react"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { stateNames } from "@/lib/api"

export function Status({ value }: { value: string }) {
  const group = ["ACTIVE", "PASSED", "matched", "applied"].includes(value)
    ? "good"
    : [
          "IN_REVIEW",
          "NEEDS_INFO",
          "FAILED",
          "TECHNICAL",
        "technical",
          "TIMED_OUT",
          "conflicting",
          "invalid",
          "missing",
          "unreadable",
        ].includes(value)
      ? "attention"
      : value === "REJECTED"
        ? "closed"
        : "pending"
  const Icon =
    group === "good"
      ? CircleCheck
      : group === "attention"
        ? AlertCircle
        : group === "pending"
          ? Clock3
          : Circle
  const words: Record<string, string> = {
    matched: "Matches form",
    conflicting: "Conflict",
    invalid: "Invalid",
    missing: "Missing",
    unreadable: "Unreadable",
    technical: "Reader unavailable",
    pending: "Reading",
    PASSED: "Passed",
    FAILED: "Failed",
    TECHNICAL: "Technical issue",
    TIMED_OUT: "Timed out",
    PENDING: "Pending",
    applied: "Applied",
  }
  return (
    <Badge variant="outline" className={"status-pill " + group}>
      <Icon aria-hidden="true" />
      {stateNames[value] || words[value] || value}
    </Badge>
  )
}

export function ErrorNotice({
  text,
  retry,
}: {
  text: string
  retry?: () => void
}) {
  return (
    <Alert className="error-notice" role="alert">
      <AlertCircle />
      <AlertTitle>Unable to complete request</AlertTitle>
      <AlertDescription>
        {text}
        {retry && (
          <Button size="sm" variant="outline" onClick={retry}>
            Try again
          </Button>
        )}
      </AlertDescription>
    </Alert>
  )
}
export function Loading({ label = "Loading case" }: { label?: string }) {
  return (
    <div className="loading" role="status">
      <LoaderCircle className="spin" />
      {label}
    </div>
  )
}
