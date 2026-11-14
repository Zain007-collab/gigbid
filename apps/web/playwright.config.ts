import { defineConfig, devices } from "@playwright/test";
import os from "node:os";
import path from "node:path";

const PORT = 8765;
const tmp = path.join(os.tmpdir(), `gigbid-e2e-${Date.now()}`);
const env = [
  "GIGBID_OFFLINE=1", "RAG_EMBED_BACKEND=hash", "GIGBID_MCP_TRANSPORT=memory", "GIGSIM_EMBEDDED=1",
  `GIGSIM_BASE_URL=http://127.0.0.1:${PORT}/gigsim`, "GIGSIM_ERROR_RATE=0", "GIGSIM_RPS=100", "GIGSIM_BURST=200",
  "GIGSIM_ADMIN_TOKEN=e2e", "WARM_REVIEWER=0", `DATABASE_URL=sqlite:///${tmp}/app.sqlite3`,
  `CHECKPOINT_DB=${tmp}/cp.sqlite3`, `MARKETPLACE_STATE_DB=${tmp}/mk.sqlite3`, `WORKSPACE_DB=${tmp}/ws.sqlite3`,
].join(" ");

export default defineConfig({
  testDir: "tests/e2e",
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]],
  use: { baseURL: `http://127.0.0.1:${PORT}`, trace: "retain-on-failure" },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile", use: { ...devices["Pixel 7"] }, grep: /@mobile/ },
  ],
  webServer: {
    command: `mkdir -p ${tmp} && cd ../.. && ${env} .venv/bin/uvicorn apps.api.main:app --host 127.0.0.1 --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}/api/health`,
    timeout: 120_000,
    reuseExistingServer: false,
  },
});
