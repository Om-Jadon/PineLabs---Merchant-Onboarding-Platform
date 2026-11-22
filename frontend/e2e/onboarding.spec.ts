import { expect, test } from "@playwright/test"

test("the clean sample submits the matching Riverbend PNGs", async ({ page }) => {
  await page.request.post("/api/demo/session", { data: { persona: "merchant" } })
  await page.goto("/#/apply")
  await page.getByRole("button", { name: "Load clean sample" }).click()
  await expect(page.getByLabel("Legal business name")).toHaveValue("Riverbend Stationery")
  await expect(page.getByLabel("PAN", { exact: true })).toHaveValue("DEMFR1234K")
  await expect(page.getByLabel("GSTIN", { exact: true })).toHaveValue("29DEMFR1234K2ZK")
  const submitted = page.waitForResponse(r =>
    r.url().endsWith("/api/applications") && r.request().method() === "POST",
  )
  await page.getByRole("button", { name: "Submit application" }).click()
  const result = await submitted
  expect(result.status()).toBe(201)
  const { documents } = result.request().postDataJSON()
  expect(documents.map((doc: { filename: string }) => doc.filename)).toEqual(["pan-stat.png", "gst-stationary.png"])
  for (const doc of documents) {
    const source = await page.request.get("/api/demo/files/" + doc.filename)
    expect(source.headers()["content-type"]).toContain("image/png")
    expect(doc.kind).toBe("AUTO")
    expect(Buffer.from(doc.content_base64, "base64").equals(await source.body())).toBe(true)
  }
})

test("the two specimen images open in review before any provider checks", async ({ page }) => {
  await page.goto("/")
  await page.getByRole("button", { name: "Merchant portal" }).click()
  await page.getByRole("button", { name: "New application" }).click()
  await page.getByRole("button", { name: "Load mismatched sample" }).click()
  await expect(page.getByRole("button", { name: "Remove pan.png" })).toBeVisible()
  await expect(page.getByRole("button", { name: "Remove gst-cert.png" })).toBeVisible()
  await page.getByRole("button", { name: "Submit application" }).click()
  await page.getByRole("button", { name: "Operator view" }).click()
  await expect(page.locator(".title-inline .status-pill")).toHaveText("Needs review")
  for (const [name, label] of [["pan.png", "Holder name"], ["gst-cert.png", "Legal name"]]) {
    await page.getByLabel("Choose evidence revision").selectOption({ label: name + " · v1" })
    await page.getByRole("button", { name: "View source for " + label }).click()
    const dialog = page.getByRole("dialog")
    await expect(dialog).toContainText(name)
    await expect.poll(() => dialog.locator("img").evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0)
    await dialog.getByRole("button", { name: "Close" }).click()
  }
  const response = await page.request.get("/api/operator/cases/" + page.url().split("/").at(-1))
  const detail = await response.json()
  expect(detail.attempts).toHaveLength(0)
  expect(detail.mid).toBeNull()
})

test("a merchant corrects an inactive registration and receives exactly one MID", async ({
  page,
}, info) => {
  const pageErrors: string[] = []
  page.on("pageerror", (error) => pageErrors.push(error.message))
  await page.goto("/")
  await page.getByRole("button", { name: "Merchant portal" }).click()
  await page.getByRole("button", { name: "New application" }).click()
  await page.getByRole("button", { name: "Load inactive registration" }).click()
  await expect(page.getByLabel("Legal business name")).toHaveValue(
    "Riverbend Stationery",
  )
  await page.getByRole("button", { name: "Submit application" }).click()
  await expect(
    page.getByRole("heading", { name: "Riverbend Stationery" }),
  ).toBeVisible()
  await page.getByRole("button", { name: "Operator view" }).click()
  await expect(
    page.getByText("GST registration inactive", { exact: true }),
  ).toBeVisible()
  await expect(
    page.locator(".provider-row").filter({ hasText: "PAN identity" }),
  ).toContainText("Passed")
  const evidence = page.locator(".document-panel")
  await expect(evidence.getByRole("heading", { name: "Structured GST document" })).toBeVisible()
  await page.screenshot({
    path: info.outputPath("review-desktop.png"),
    fullPage: true,
  })
  await page.getByRole("button", { name: "View source for GSTIN" }).click()
  await expect(page.getByRole("dialog")).toContainText("gst-record.json")
  await page.getByRole("button", { name: "Close" }).click()
  await page
    .getByRole("button", { name: "Request current certificate" })
    .click()
  await page.getByRole("button", { name: "Send request" }).click()
  await page.getByRole("button", { name: "Open merchant correction" }).click()
  await page.getByRole("button", { name: "Load current GST record" }).click()
  await expect(page.getByLabel("GSTIN", { exact: true })).toHaveValue(
    "29DEMFR1234K2ZK",
  )
  await page.getByRole("button", { name: "Submit correction" }).click()
  await expect(page.locator(".title-inline .status-pill")).toHaveText("Active")
  const mid = await page.locator(".case-meta .mono").innerText()
  expect(mid).toMatch(/^MID-/)
  const caseId = page.url().split("/").at(-1)
  await page.getByRole("button", { name: "Operator view" }).click()
  await page.getByRole("button", { name: "Verification", exact: true }).click()
  await page.getByRole("button", { name: "Replay latest callback" }).click()
  await expect(
    page.getByText("applied · 2 deliveries", { exact: true }),
  ).toBeVisible()
  await page.reload()
  await expect(page.locator(".case-meta .mono")).toHaveText(mid)
  const response = await page.request.get("/api/operator/cases/" + caseId)
  expect(response.ok()).toBeTruthy()
  const detail = await response.json()
  expect(
    detail.attempts.filter((a: { kind: string }) => a.kind === "PAN"),
  ).toHaveLength(1)
  expect(
    detail.attempts.filter((a: { kind: string }) => a.kind === "GST"),
  ).toHaveLength(2)
  expect(
    detail.events.filter(
      (e: { title: string }) => e.title === "Merchant ID issued",
    ),
  ).toHaveLength(1)
  await page.getByRole("button", { name: "Activity", exact: true }).click()
  await expect(page.locator(".timeline")).toContainText("Merchant ID issued")
  expect(pageErrors).toEqual([])
})

test("case evidence fits a mobile viewport", async ({ page }, info) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto("/")
  await expect(
    page.getByRole("heading", { name: "Source document" }),
  ).toBeVisible()
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(390)
  await page.getByRole("button", { name: "View document", exact: true }).click()
  await expect(page.getByRole("dialog")).toBeVisible()
  const box = await page.getByRole("dialog").boundingBox()
  expect(box).toBeTruthy()
  expect(box!.x).toBeGreaterThanOrEqual(-1)
  expect(box!.x + box!.width).toBeLessThanOrEqual(391)
  await page.screenshot({ path: info.outputPath("mobile-source.png") })
})
