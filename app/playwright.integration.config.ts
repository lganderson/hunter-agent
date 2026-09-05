import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./integration",
  workers: 1,
  reporter: "list",
  use: {
    ...devices["Desktop Chrome"],
    baseURL: "http://127.0.0.1:4175",
    screenshot: "only-on-failure",
    trace: "retain-on-failure"
  },
  webServer: {
    command: "python3 ../scripts/demo_preview.py --port 4175 --workflow-fixtures",
    url: "http://127.0.0.1:4175/api/health",
    reuseExistingServer: false
  }
});
