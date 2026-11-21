import { defineConfig } from "@playwright/test"

export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  expect: { timeout: 50_000 },
  workers: 1,
  use: {
    baseURL: process.env.DESK_URL || "http://127.0.0.1:8018",
    viewport: { width: 1440, height: 990 },
    trace: "retain-on-failure",
    video: process.env.RECORD_DEMO
      ? { mode: "on", size: { width: 1440, height: 990 } }
      : "off",
  },
})
