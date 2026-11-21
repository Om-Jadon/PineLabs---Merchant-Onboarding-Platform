import { randomUUID } from "node:crypto"
import { expect, test, type APIRequestContext } from "@playwright/test"
import type { CaseDetail, Samples } from "../src/lib/api"

async function submit(request: APIRequestContext, scenario: string) {
  await request.post("/api/demo/session", { data: { persona: "merchant" } })
  const fixtures = await request.get("/api/demo/fixtures")
  const { legal_name, pan, current_gstin: gstin }: Samples = await fixtures.json()
  const response = await request.post("/api/applications", {
    data: {
      submission_key: randomUUID(), scenario, legal_name, pan, gstin,
      documents: [
        { kind: "PAN", filename: "pan.json", fields: { holder_name: legal_name, pan } },
        { kind: "GST", filename: "gst.json", fields: { legal_name, gstin } },
      ],
    },
  })
  expect(response.status()).toBe(201)
  const { id } = await response.json()
  await request.post("/api/demo/session", { data: { persona: "operator" } })
  const read = async (): Promise<CaseDetail> => {
    const detail = await request.get("/api/operator/cases/" + id)
    expect(detail.ok()).toBeTruthy()
    return detail.json()
  }
  await expect.poll(async () => (await read()).state).toBe("ACTIVE")
  return read()
}

test("an expired GST callback arrives over HTTP and the new attempt activates the case", async ({ request }) => {
  const detail = await submit(request, "late_callback")
  expect(detail.receipts.some(r => r.kind === "GST" && r.disposition === "late")).toBeTruthy()
  expect(detail.attempts.filter(a => a.kind === "GST").map(a => a.status)).toEqual(["TIMED_OUT", "PASSED"])
  expect(detail.mid).toMatch(/^MID-/)
})

test("the registry issues an MID, loses its first response and recovers the same issuance", async ({ request }) => {
  const detail = await submit(request, "lost_mid_response")
  expect(detail.events.some(e => e.title === "Issue retry scheduled")).toBeTruthy()
  expect(detail.events.filter(e => e.title === "Merchant ID issued")).toHaveLength(1)
  expect(detail.events.filter(e => e.title.endsWith("→ APPROVED"))).toHaveLength(1)
  expect(detail.issuance).toBe("ISSUED")
  expect(detail.mid).toMatch(/^MID-/)
})
