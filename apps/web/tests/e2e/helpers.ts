import { expect, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const dev = fs.readFileSync(path.resolve(__dirname, "../../../../evals/datasets/dev/ai_ml.jsonl"), "utf8")
  .trim().split("\n").map((l) => JSON.parse(l));
export const bidJobs: string[] = dev.filter((r) => r.gold.bid && r.gold.coverage === 1 && !r.gold.injection).map((r) => r.job_id);

export async function signUpAndOnboard(page: Page, persona = "AI/ML engineer") {
  const email = `e2e_${Date.now()}_${Math.random().toString(36).slice(2, 7)}@test.dev`;
  await page.goto("/login");
  await page.getByRole("button", { name: /create an account/i }).click();
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("e2e-password-123");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("heading", { name: "Set up GigBid" })).toBeVisible();
  await page.getByText(persona).click();
  await page.getByTestId("finish-onboarding").click();
  await expect(page.getByRole("heading", { name: "Job inbox" })).toBeVisible();
  return email;
}

/** Start analysis of a job that should be bid on, then open the proposal studio once the draft waits for approval. */
export async function draftWaitingForApproval(page: Page, jobId: string) {
  const r = await page.request.post(`/api/jobs/${jobId}/analyze`);
  expect(r.ok()).toBeTruthy();
  await expect.poll(async () => (await (await page.request.get(`/api/jobs/${jobId}`)).json()).status, { timeout: 30_000 })
    .toBe("awaiting_approval");
  await page.goto(`/studio/${jobId}`);
  await expect(page.getByTestId("approval-panel")).toBeVisible();
}
