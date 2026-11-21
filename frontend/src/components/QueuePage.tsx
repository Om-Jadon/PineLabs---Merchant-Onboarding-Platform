import { useState } from "react"
import { ClipboardList, FilePlus2, Search } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { ErrorNotice, Loading, Status } from "./common"
import {
  dateTime,
  elapsed,
  stateNames,
  type Summary,
  useResource,
} from "@/lib/api"

export function QueuePage({
  operator,
  newApplication,
}: {
  operator: boolean
  newApplication: () => void
}) {
  const { data, error, reload } = useResource<Summary[]>(
    operator ? "/api/operator/cases" : "/api/applications",
    true,
  )
  const [filter, setFilter] = useState("")
  const [search, setSearch] = useState("")
  const items = (data || []).filter(
    (c) =>
      (!filter || c.state === filter) &&
      (c.legal_name + " " + c.reference)
        .toLowerCase()
        .includes(search.toLowerCase()),
  )
  return (
    <>
      <div className="breadcrumb">
        <span>{operator ? "Operations" : "Merchant portal"}</span>
      </div>
      <div className="page-heading">
        <div>
          <h1>{operator ? "Review queue" : "My applications"}</h1>
          <p className="subtext">
            {operator
              ? "Track verification progress and resolve merchant exceptions."
              : "Track applications and respond to requests for evidence."}
          </p>
        </div>
        <Button onClick={newApplication}>
          <FilePlus2 />
          New application
        </Button>
      </div>
      {error && <ErrorNotice text={error} retry={reload} />}
      {!data ? (
        <Loading label="Loading applications" />
      ) : (
        <section className="panel queue-panel">
          <div className="queue-tools">
            <div className="search-box">
              <Search />
              <input
                aria-label="Search applications"
                placeholder="Search merchants or references"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
            <select
              aria-label="Filter by status"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            >
              <option value="">All statuses</option>
              {Object.entries(stateNames).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
          </div>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Merchant</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="wide-col">Current reason</TableHead>
                <TableHead className="wide-col">Next actor</TableHead>
                <TableHead>Time in state</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((c) => (
                <TableRow key={c.id}>
                  <TableCell>
                    <a className="case-link" href={"#/case/" + c.id}>
                      {c.legal_name}
                    </a>
                    <small className="mono">{c.reference}</small>
                  </TableCell>
                  <TableCell>
                    <Status value={c.state} />
                  </TableCell>
                  <TableCell className="wide-col">{c.reason}</TableCell>
                  <TableCell className="wide-col">{c.next_actor}</TableCell>
                  <TableCell title={dateTime(c.state_since)}>
                    {elapsed(c.state_since)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {!items.length && (
            <div className="empty">
              <ClipboardList />
              <h3>
                {data.length
                  ? "No applications match"
                  : "Your first application"}
              </h3>
              <p>
                {data.length
                  ? "Adjust the status filter or search."
                  : "Create an application to begin verification."}
              </p>
            </div>
          )}
          <div className="table-foot">
            {items.length} {items.length === 1 ? "application" : "applications"}
          </div>
        </section>
      )}
    </>
  )
}
