import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  workers: 1,
  timeout: 90000,
  use: {
    baseURL: "http://127.0.0.1:8766",
    browserName: "chromium",
    channel: process.env.PLAYWRIGHT_CHANNEL || undefined,
    viewport: { width: 1440, height: 1100 },
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  webServer: {
    command:
      `"${process.env.TRACERAG_TEST_PYTHON || "python"}" ../tests/serve_console_fixture.py`,
    url: "http://127.0.0.1:8766/api/bootstrap",
    timeout: 60000,
    reuseExistingServer: false,
  },
});
