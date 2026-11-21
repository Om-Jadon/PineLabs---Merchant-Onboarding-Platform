import { randomUUID } from "node:crypto"
import { expect, test } from "@playwright/test"

test("switching workspaces updates other tabs and access loss clears an open case", async ({ page, context }) => {
  await page.goto("/")
  await expect(page.getByRole("heading", { name: "Source document" })).toBeVisible()
  const other = await context.newPage()
  await other.goto("/#/queue")
  await other.getByRole("button", { name: "Merchant portal" }).click()
  await expect(page.getByRole("heading", { name: "My applications" })).toBeVisible()

  const name = "Access check " + randomUUID()
  const reply = await context.request.post("/api/applications", {
    data: { submission_key: randomUUID(), legal_name: name },
  })
  expect(reply.status()).toBe(201)
  const { id } = await reply.json()
  await page.goto("/#/case/" + id)
  await expect(page.getByRole("heading", { name })).toBeVisible()
  await context.request.post("/api/demo/session", { data: { persona: "other_merchant" } })
  await expect(page.getByText("Application not found.")).toBeVisible()
  await expect(page.getByRole("heading", { name })).toHaveCount(0)
})

test("a stale correction can reload the current version", async ({ page }) => {
  await page.request.post("/api/demo/session", { data: { persona: "merchant" } })
  const reply = await page.request.post("/api/applications", { data: { submission_key: randomUUID(), legal_name: "First draft" } })
  expect(reply.status()).toBe(201)
  const { id, version } = await reply.json()
  await page.goto("/#/correct/" + id)
  await expect(page.getByLabel("Legal business name")).toHaveValue("First draft")
  const update = await page.request.post("/api/applications/" + id + "/corrections", { data: { expected_version: version, legal_name: "Current draft" } })
  expect(update.ok()).toBeTruthy()
  await page.getByLabel("Legal business name").fill("Older edit")
  await page.getByRole("button", { name: "Submit correction" }).click()
  await page.getByRole("button", { name: "Reload current application" }).click()
  await expect(page.getByLabel("Legal business name")).toHaveValue("Current draft")
})

test("a PAN request uses a matching message and replacement sample", async ({ page }) => {
  await page.request.post("/api/demo/session", { data: { persona: "merchant" } })
  const samples = await page.request.get("/api/demo/fixtures")
  const { legal_name, pan, current_gstin: gstin } = await samples.json()
  const reply = await page.request.post("/api/applications", {
    data: { submission_key: randomUUID(), legal_name, pan, gstin, documents: [
      { kind: "PAN", filename: "pan.json", fields: { holder_name: "Earlier holder", pan } },
      { kind: "GST", filename: "gst.json", fields: { legal_name, gstin } },
    ] },
  })
  expect(reply.status()).toBe(201)
  const { id } = await reply.json()
  await page.request.post("/api/demo/session", { data: { persona: "operator" } })
  await page.goto("/#/case/" + id)
  await page.getByRole("button", { name: "Request evidence" }).click()
  const picker = page.getByLabel("Requested evidence")
  await picker.selectOption("GST")
  await expect(page.getByLabel("Message to merchant")).toHaveValue(/current GST document/)
  await picker.selectOption("PAN")
  await expect(page.getByLabel("Message to merchant")).toHaveValue(/current PAN document/)
  await page.getByRole("button", { name: "Send request" }).click()
  await page.getByRole("button", { name: "Open merchant correction" }).click()
  await page.getByRole("button", { name: "Load PAN record" }).click()
  await expect(page.getByText("pan.json", { exact: true }).first()).toBeVisible()
  await page.getByRole("button", { name: "Submit correction" }).click()
  await expect(page.locator(".title-inline .status-pill")).toHaveText("Active")
})
