import { expect, test, type Page } from "@playwright/test";

import { syntheticAppState } from "../src/test/fixtures/appState";

async function installSyntheticApi(page: Page) {
  await page.route("**/api/**", async route => {
    const url = new URL(route.request().url());
    const pathname = url.pathname;
    if (pathname === "/api/app-shell") {
      const {
        company_posting_candidates: _companyCandidates,
        discovery_candidates: _discoveryCandidates,
        company_career_scans: _careerScans,
        ...shell
      } = syntheticAppState;
      await route.fulfill({ json: {
        ...shell,
        api_version: 1,
        revision: 1,
        candidate_counts: {
          company: syntheticAppState.company_posting_candidates.length,
          discovery: syntheticAppState.discovery_candidates.length
        },
        audit: { stable_revision: true, omitted_large_fields: ["candidate pools", "notes"] }
      } });
      return;
    }
    if (pathname === "/api/candidates/company") {
      await route.fulfill({ json: candidatePage("company", []) });
      return;
    }
    if (pathname === "/api/candidates/discovery") {
      const items = syntheticAppState.discovery_candidates.map(candidate => ({
        ...candidate,
        company: syntheticAppState.companies.find(company => company.id === candidate.company_id) || null,
        description_truncated: false
      }));
      await route.fulfill({ json: candidatePage("discovery", items, url.searchParams.get("search_id") || "") });
      return;
    }
    if (pathname === "/api/candidates/discovery/detail") {
      const candidate = syntheticAppState.discovery_candidates.find(item => item.id === url.searchParams.get("id"));
      await route.fulfill({ json: {
        api_version: 1,
        pool: "discovery",
        revision: 1,
        item: candidate ? {
          ...candidate,
          company: syntheticAppState.companies.find(company => company.id === candidate.company_id) || null
        } : null,
        audit: { stable_revision: true, excluded_company: false, includes_full_description: true, includes_notes: true }
      } });
      return;
    }
    if (pathname === "/api/companies/detail") {
      const company = syntheticAppState.companies.find(item => item.id === url.searchParams.get("id"));
      await route.fulfill({ json: {
        api_version: 1,
        resource: "company",
        revision: 1,
        item: company ? { ...company, company_career_source: null } : null,
        audit: { stable_revision: true, includes_omitted_fields: true }
      } });
      return;
    }
    if (pathname === "/api/companies/discovery-jobs/current" || pathname === "/api/discovery/jobs/current") {
      await route.fulfill({ json: { job: null } });
      return;
    }
    await route.fulfill({ status: 404, json: { error: `No synthetic response for ${pathname}` } });
  });
}

function candidatePage(pool: "company" | "discovery", items: unknown[], searchId = "") {
  return {
    api_version: 1,
    pool,
    revision: 1,
    items,
    counts: {
      source: items.length,
      eligible: items.length,
      canonical: items.length,
      filtered: items.length,
      returned: items.length,
      excluded_companies: 0,
      out_of_scope: 0,
      ignored_sources: 0
    },
    facets: { statuses: [], tracking: [], companies: [] },
    page: { limit: 50, offset: 0, has_more: false, next_cursor: "" },
    audit: {
      stable_revision: true,
      filters: {
        search: "",
        status: [],
        minimum_fit_score: 0,
        tracking_status: "",
        company_id: "",
        include_excluded_companies: false,
        include_out_of_scope: false,
        search_id: searchId
      },
      canonical_hidden_count: 0,
      search_context: searchId ? { id: searchId, name: searchId, affects_rows: false } : null
    }
  };
}

test.beforeEach(async ({ page }) => {
  await installSyntheticApi(page);
});

test("candidate modes expose route headings and native pressed-button semantics", async ({ page }) => {
  const legacyRequests: string[] = [];
  page.on("request", request => {
    if (new URL(request.url()).pathname === "/api/app-state") legacyRequests.push(request.url());
  });
  await page.goto("/candidates");

  await expect(page.getByRole("heading", { level: 1, name: "Tracked company candidates" })).toHaveCount(1);
  const modeGroup = page.getByRole("group", { name: "Candidate source mode" });
  await expect(modeGroup).toBeVisible();
  await expect(modeGroup.getByRole("button", { name: "Tracked Companies" })).toHaveAttribute("aria-pressed", "true");

  const discoveryButton = modeGroup.getByRole("button", { name: "Discovery" });
  await discoveryButton.focus();
  await page.keyboard.press("Enter");

  await expect(page).toHaveURL(/\/candidates\?mode=discovery$/);
  await expect(page.getByRole("heading", { level: 1, name: "Discovery candidates" })).toHaveCount(1);
  await expect(discoveryButton).toHaveAttribute("aria-pressed", "true");
  expect(legacyRequests).toEqual([]);
});

test("switching the acquisition search preserves the shared Discovery review queue", async ({ page }) => {
  await page.goto("/candidates?mode=discovery&search_id=DS1001");

  const search = page.getByRole("combobox", { name: "Search" });
  await expect(search).toHaveValue("DS1001");
  const candidateTitles = page.locator(".discovery-table tbody .candidate-title-cell strong");
  const before = await candidateTitles.allTextContents();
  expect(before).toEqual(["Principal Platform Product Lead", "Senior Technical Program Lead"]);

  await search.selectOption("DS1002");

  await expect(page).toHaveURL(/search_id=DS1002/);
  await expect(search).toHaveValue("DS1002");
  await expect(candidateTitles).toHaveText(before);
});

test("uncertain location candidates stay visible with a verification action", async ({ page }) => {
  const base = syntheticAppState.discovery_candidates[0];
  const candidate = {
    ...base,
    id: "DC1099",
    title: "Director, Platform Delivery",
    location: "",
    work_mode: "",
    lane_match: "",
    qualification_status: "needs-verification" as const,
    qualification_reason: "location eligibility still needs verification",
    review_state: "needs-qualification" as const,
    review_next_action: "Verify the posting location and work mode",
    recommendation_eligible: false
  };
  await page.route("**/api/candidates/discovery?**", route => route.fulfill({
    json: candidatePage("discovery", [{
      ...candidate,
      company: syntheticAppState.companies.find(company => company.id === candidate.company_id) || null,
      description_truncated: false
    }], "DS1001")
  }));

  await page.goto("/candidates?mode=discovery&search_id=DS1001");

  await expect(page.getByText("Director, Platform Delivery", { exact: true })).toBeVisible();
  await expect(page.getByText("Needs location verification")).toBeVisible();
  await expect(page.getByRole("button", { name: "Verify" })).toBeVisible();
});

test("candidate selection controls have accessible names and minimum targets", async ({ page }) => {
  await page.goto("/candidates?mode=discovery&search_id=DS1001");

  const checkbox = page.getByRole("checkbox", { name: "Select Principal Platform Product Lead at Atlas Labs" });
  await expect(checkbox).toBeVisible();
  const size = await checkbox.evaluate(element => {
    const rect = element.getBoundingClientRect();
    return { width: rect.width, height: rect.height };
  });
  expect(size.width).toBeGreaterThanOrEqual(24);
  expect(size.height).toBeGreaterThanOrEqual(24);

  await checkbox.focus();
  await page.keyboard.press("Space");
  await expect(checkbox).toBeChecked();
});

test("bulk tracked-company check skips not-interested companies", async ({ page }) => {
  const eligible = syntheticAppState.companies[0];
  const excluded = { ...syntheticAppState.companies[1], interest_status: "not-interested" as const };
  const checkedIds: string[] = [];
  await page.route("**/api/app-shell", route => {
    const {
      company_posting_candidates: _companyCandidates,
      discovery_candidates: _discoveryCandidates,
      company_career_scans: _careerScans,
      ...shell
    } = syntheticAppState;
    return route.fulfill({ json: {
      ...shell,
      companies: [eligible, excluded],
      api_version: 1,
      revision: 1,
      candidate_counts: { company: 0, discovery: syntheticAppState.discovery_candidates.length },
      audit: { stable_revision: true, omitted_large_fields: ["candidate pools", "notes"] }
    } });
  });
  await page.route("**/api/companies/check", async route => {
    const body = route.request().postDataJSON() as { id: string };
    checkedIds.push(body.id);
    await route.fulfill({ json: {
      company: eligible,
      career_source: null,
      candidates: [],
      new: [],
      recommended: [],
      unavailable_count: 0,
      verification_count: 0,
      verification_skipped_count: 0,
      scan: {}
    } });
  });

  await page.goto("/candidates");
  await page.getByRole("button", { name: "Check tracked companies" }).click();

  await expect(page.getByRole("status")).toContainText("Checked 1 companies");
  await expect(page.getByRole("status")).toContainText("1 skipped");
  expect(checkedIds).toEqual([eligible.id]);
});

test("candidate review fetches the selected heavy detail only", async ({ page }) => {
  const detailRequests: string[] = [];
  page.on("request", request => {
    if (new URL(request.url()).pathname === "/api/candidates/discovery/detail") {
      detailRequests.push(request.url());
    }
  });
  await page.goto("/candidates?mode=discovery&search_id=DS1001");

  await page.getByRole("button", { name: "Review" }).first().click();

  await expect(page.getByRole("dialog", { name: "Principal Platform Product Lead" })).toBeVisible();
  expect(detailRequests).toHaveLength(1);
  expect(new URL(detailRequests[0]).searchParams.get("id")).toBe("DC1001");
});

test("company detail uses scoped candidate lists and the full company detail resource", async ({ page }) => {
  const apiRequests: string[] = [];
  page.on("request", request => {
    const url = new URL(request.url());
    if (url.pathname.includes("/detail") || url.pathname.startsWith("/api/candidates/")) {
      apiRequests.push(request.url());
    }
  });
  await page.goto("/companies/CO1001");

  await expect(page.getByRole("heading", { level: 1, name: "Atlas Labs" })).toBeVisible();
  expect(apiRequests.some(request => new URL(request).pathname === "/api/companies/detail")).toBe(true);
  const candidateRequests = apiRequests.filter(request => new URL(request).pathname.startsWith("/api/candidates/"));
  expect(new Set(candidateRequests.map(request => new URL(request).pathname))).toEqual(new Set([
    "/api/candidates/company",
    "/api/candidates/discovery"
  ]));
  expect(candidateRequests.every(request => new URL(request).searchParams.get("company_id") === "CO1001")).toBe(true);
});

test("company detail research uses OpenAI and reports the evaluation result", async ({ page }) => {
  const company = syntheticAppState.companies[0];
  let requestBody: { id?: string; provider?: string } = {};
  await page.route("**/api/companies/research", async route => {
    requestBody = route.request().postDataJSON() as { id?: string; provider?: string };
    await route.fulfill({ json: {
      company: { ...company, industry: "Software Development", company_evaluation_status: "ready" },
      applied_fields: ["industry"],
      suggestions: [],
      source_url: "https://example.invalid/company",
      provider: "openai",
      run_id: "company-evaluation-test",
      evaluation_status: "ready"
    } });
  });

  await page.goto(`/companies/${company.id}`);
  await page.getByRole("button", { name: "Research company" }).click();

  await expect(page.getByRole("status")).toContainText("Research complete");
  await expect(page.getByRole("status")).toContainText("Evaluation: ready");
  expect(requestBody).toEqual({ id: company.id });
});

test("primary desktop routes expose one level-one heading", async ({ page }) => {
  const routes = [
    ["/postings", "Postings"],
    ["/companies", "Companies"],
    ["/actions", "Actions"],
    ["/contacts", "Contacts"]
  ] as const;

  for (const [path, heading] of routes) {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1, name: heading })).toHaveCount(1);
  }
});

test.fixme("Discovery results remain reachable at a 390 by 844 viewport", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/candidates?mode=discovery&search_id=DS1001");

  const results = page.locator("#candidates-view .table-scroll");
  await expect(results).toBeVisible();
  const clientHeight = await results.evaluate(element => element.clientHeight);
  expect(clientHeight).toBeGreaterThan(0);
  await expect(page.getByText("Principal Platform Product Lead", { exact: true })).toBeVisible();
});
