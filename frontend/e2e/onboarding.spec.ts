import { expect, test } from "@playwright/test"

test("a merchant corrects an inactive registration and receives exactly one MID", async ({
  page,
}, info) => {
  const pageErrors: string[] = []
  page.on("pageerror", (error) => pageErrors.push(error.message))
  await page.goto("/")
  await page.getByRole("button", { name: "Merchant portal" }).click()
  await page.getByRole("button", { name: "New application" }).click()
  await page.getByRole("button", { name: "Load review sample" }).click()
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
  await expect(evidence.locator(".paper-crop img")).toBeVisible()
  await expect
    .poll(() =>
      evidence
        .locator(".paper-crop img")
        .evaluate((img: HTMLImageElement) => img.naturalWidth),
    )
    .toBeGreaterThan(0)
  await page.screenshot({
    path: info.outputPath("review-desktop.png"),
    fullPage: true,
  })
  await page.getByRole("button", { name: "View source for GSTIN" }).click()
  await expect(page.getByRole("dialog")).toContainText("gst-inactive.pdf")
  await page.getByRole("button", { name: "Close" }).click()
  await page
    .getByRole("button", { name: "Request current certificate" })
    .click()
  await page.getByRole("button", { name: "Send request" }).click()
  await page.getByRole("button", { name: "Open merchant correction" }).click()
  await page.getByRole("button", { name: "Load current certificate" }).click()
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
