import { defineConfig, devices } from "@playwright/test";

const host = "127.0.0.1";
const port = 4174;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  reporter: "list",
  use: {
    baseURL: `http://${host}:${port}`,
    screenshot: "only-on-failure",
    trace: "retain-on-failure"
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] }
    }
  ],
  webServer: {
    command: "npm run dev:e2e",
    url: `http://${host}:${port}`,
    reuseExistingServer: !process.env.CI,
    timeout: 30_000
  }
});
