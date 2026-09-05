import { expect, test } from "@playwright/test";

test("a contact edit survives a real database save and browser reload", async ({ page, request }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  const created = await request.post("/api/contacts/upsert", { data: { updates: { name: "Integration Example", notes: "Original note" } } });
  expect(created.ok()).toBeTruthy();
  const { contact } = await created.json();
  await page.goto("/contacts");
  await page.getByRole("button", { name: /Integration Example/ }).click();
  await expect(page.getByRole("textbox", { name: "Notes", exact: true })).toHaveValue("Original note");
  await page.getByRole("textbox", { name: "Notes", exact: true }).fill("Saved through the browser");
  await page.getByRole("button", { name: "Save Contact", exact: true }).click();
  await expect.poll(async () => {
    const response = await request.get(`/api/contacts/detail?id=${contact.id}`);
    return (await response.json()).item.notes;
  }).toBe("Saved through the browser");
  await page.reload();
  await page.getByRole("button", { name: /Integration Example/ }).click();
  await expect(page.getByRole("textbox", { name: "Notes", exact: true })).toHaveValue("Saved through the browser");
  expect(errors).toEqual([]);
});

test("Discovery pages real SQLite results and persists a review decision", async ({ page, request }) => {
  await page.goto("/candidates?mode=discovery");
  await expect(page.getByRole("button", { name: "Load more candidates", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Load more candidates", exact: true }).click();
  await expect(page.getByRole("button", { name: "Load more candidates", exact: true })).toHaveCount(0);
  const row = page.getByRole("row").filter({ has: page.getByRole("link", { name: "Example Studio 01", exact: true }) });
  const decision = page.waitForResponse(response => response.url().endsWith("/api/discovery/candidates/update") && response.request().method() === "POST");
  await row.getByRole("button", { name: "Ignore", exact: true }).click();
  expect((await decision).ok()).toBeTruthy();
  await expect(row).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Needs decision 59", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Ignored 1", exact: true })).toBeVisible();
  await expect(page.getByText(/shown from 59 matching roles/)).toBeVisible();
  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await expect(row).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Needs decision 60", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Ignored 0", exact: true })).toBeVisible();
  await row.getByRole("button", { name: "Ignore", exact: true }).click();
  await expect(row).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Ignored 1", exact: true })).toBeVisible();
  await page.reload();
  const saved = await request.get("/api/candidates/discovery/detail?id=DC9000");
  expect((await saved.json()).item.status).toBe("ignored");
  await expect(page.getByRole("checkbox", { name: /Select Principal Product Manager at Example Studio 01/ })).toHaveCount(0);
});
