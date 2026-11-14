import { expect, test } from "@playwright/test";
import { bidJobs, draftWaitingForApproval, signUpAndOnboard } from "./helpers";

test("onboarding: sign up, pick a persona, land on an empty inbox @mobile", async ({ page }) => {
  await signUpAndOnboard(page);
  await expect(page.getByText("No jobs yet.")).toBeVisible();
  await page.getByRole("button", { name: "Scout now" }).click();
  await expect(page.getByRole("status")).toContainText(/queued \d+ new jobs/);
});

test("approve a proposal: evidence on hover, dry-run preview, submitted exactly once", async ({ page }) => {
  await signUpAndOnboard(page);
  const job = bidJobs[0];
  await draftWaitingForApproval(page, job);
  await expect(page.getByTestId("dry-run")).toContainText(job);
  await expect(page.getByTestId("connects-cost")).toHaveText(/\d+/);
  const cited = page.locator(".claim.supported").first();
  await cited.hover();
  await expect(page.getByRole("tooltip")).toContainText(/case_study|repo|code|review|proposal/);
  await page.getByTestId("approve").click();
  await expect.poll(async () => (await (await page.request.get(`/api/jobs/${job}`)).json()).status, { timeout: 30_000 }).toBe("submitted");
  const again = await page.request.post(`/api/approvals/proposal/${job}`, { data: { action: "approve" } });
  expect(again.status()).toBe(409);                       // nothing left to approve: no double submit
});

test("reject with a reason: nothing is sent and the reason is recorded", async ({ page }) => {
  await signUpAndOnboard(page);
  const job = bidJobs[1];
  await draftWaitingForApproval(page, job);
  await page.getByTestId("reject").click();
  await page.getByTestId("reject-reason").fill("budget too low for the scope");
  await page.getByTestId("confirm-reject").click();
  await expect.poll(async () => (await (await page.request.get(`/api/jobs/${job}`)).json()).status, { timeout: 30_000 }).toBe("rejected");
  const pipeline = await (await page.request.get("/api/pipeline")).json();
  expect(pipeline.filter((c: { column: string }) => c.column !== "drafted")).toHaveLength(0);
  await page.goto("/analytics");
  await expect(page.getByText("budget too low for the scope")).toBeVisible();
});

test("edit a draft: the edit is re-checked and then submitted", async ({ page }) => {
  await signUpAndOnboard(page);
  const job = bidJobs[2];
  await draftWaitingForApproval(page, job);
  await page.getByTestId("edit").click();
  const box = page.getByTestId("edit-text");
  const text = await box.inputValue();
  await box.fill(text.split(". ").slice(1).join(". "));     // cut the first sentence
  await page.getByTestId("save-edit").click();
  await expect.poll(async () => {
    const d = await (await page.request.get(`/api/jobs/${job}`)).json();
    if (d.status === "awaiting_approval" && d.pending_approval?.note) {
      await page.request.post(`/api/approvals/proposal/${job}`, { data: { action: "approve" } });
    }
    return d.status;
  }, { timeout: 30_000 }).toBe("submitted");
});

test("degraded mode: banner appears when the marketplace is down and clears when it recovers @mobile", async ({ page }) => {
  await signUpAndOnboard(page);
  const admin = { headers: { "x-admin-token": "e2e" } };
  await page.request.post("/gigsim/admin/chaos", { ...admin, data: { down: true } });
  await page.reload();
  await expect(page.getByTestId("degraded-banner")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId("degraded-banner")).toContainText("Marketplace unavailable");
  await page.request.post("/gigsim/admin/chaos", { ...admin, data: { down: false } });
  await page.reload();
  await expect(page.getByTestId("degraded-banner")).toHaveCount(0, { timeout: 20_000 });
});
