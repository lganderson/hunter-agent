import { expect, test } from "@playwright/test";

test("Consider and Undo reconcile candidates, postings, and sidebar actions without reloading", async ({ page, request }) => {
  const initial = await (await request.get("/api/app-shell")).json();
  const considering = initial.applications.filter((row: { stage: string }) => row.stage === "considering").length;
  const openActions = initial.actions.filter((row: { is_open: boolean }) => row.is_open).length;
  await page.goto("/candidates?mode=discovery");
  const row = page.getByRole("row").filter({ has: page.getByRole("link", { name: "Example Studio 03", exact: true }) });
  const response = page.waitForResponse(response => response.url().endsWith("/api/discovery/candidates/pursue"));
  await row.getByRole("button", { name: "Consider", exact: true }).click();
  const result = await (await response).json();
  expect(result.created).toBe(true);
  await expect(row).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Needs decision 59", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Considering 1", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: `Considering ${considering + 1}`, exact: true })).toBeVisible();
  const saved = await (await request.get("/api/app-shell")).json();
  await expect(page.getByRole("link", { name: `Open actions ${saved.actions.filter((row: { is_open: boolean }) => row.is_open).length}`, exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await expect(row).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Needs decision 60", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Considering 0", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: `Considering ${considering}`, exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: `Open actions ${openActions}`, exact: true })).toBeVisible();
  expect((await request.get(`/api/applications/detail?id=${result.posting.id}`)).status()).toBe(404);
});

test("a company-detail decision refreshes cached lists and the linked Discovery record", async ({ page, request }) => {
  await page.goto("/candidates?mode=companies&fit=all&latest=false&scope=all&companies=CO9901");
  await page.getByRole("button", { name: "Ignored 0", exact: true }).click();
  await page.getByRole("button", { name: "Needs decision 1", exact: true }).click();
  await page.getByRole("link", { name: "Linked Example", exact: true }).click();
  const card = page.getByRole("article").filter({ has: page.getByRole("link", { name: "Platform Systems Lead", exact: true }) }).last();
  await card.getByRole("button", { name: "Ignore", exact: true }).click();
  await expect(card.getByRole("button", { name: "Ignored", exact: true })).toBeDisabled();
  const linked = await (await request.get("/api/candidates/discovery/detail?id=DC9901")).json();
  expect(linked.item.canonical_status).toBe("ignored");
  expect(linked.item.is_canonical).toBe(false);
  await page.getByRole("complementary", { name: "Dashboard navigation" }).getByRole("link", { name: "Candidates", exact: true }).click();
  await expect(page.getByRole("button", { name: "Needs decision 0", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Ignored 1", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Ignored 1", exact: true }).click();
  const row = page.getByRole("row").filter({ has: page.getByRole("link", { name: "Linked Example", exact: true }) });
  await expect(row).toHaveCount(1);
  await row.getByRole("button", { name: "Needs decision", exact: true }).click();
  await expect(row).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Needs decision 1", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Ignored 0", exact: true })).toBeVisible();
  const restored = await (await request.get("/api/candidates/discovery/detail?id=DC9901")).json();
  expect(restored.item.canonical_status).toBe("new");
  expect(restored.item.is_canonical).toBe(false);
});

test("bulk Ignore and restore keep selection and counts aligned", async ({ page }) => {
  await page.goto("/candidates?mode=discovery");
  const rows = ["Example Studio 04", "Example Studio 05"].map(name =>
    page.getByRole("row").filter({ has: page.getByRole("link", { name, exact: true }) })
  );
  for (const row of rows) await row.getByRole("checkbox").check();
  const bulk = page.getByRole("region", { name: "Bulk candidate actions" });
  await bulk.getByRole("button", { name: "Ignore 2", exact: true }).click();
  for (const row of rows) await expect(row).toHaveCount(0);
  await expect(bulk).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Needs decision 58", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Ignored 2", exact: true }).click();
  for (const row of rows) await row.getByRole("checkbox").check();
  await bulk.getByRole("button", { name: /Needs decision/ }).click();
  await expect(bulk).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Needs decision 60", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Ignored 0", exact: true })).toBeVisible();
});
