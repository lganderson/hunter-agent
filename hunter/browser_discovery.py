"""Search the web through the user's dedicated local Hunter Chrome window."""

import json
import random
import subprocess
import time
from urllib.parse import parse_qsl, quote_plus, urlencode, urlparse, urlunparse


BRIDGE_MARKER = "hunter_browser_bridge=1"
OSASCRIPT_PATH = "/usr/bin/osascript"
GOOGLE_PAGE_SIZE = 10
LINKEDIN_PAGE_SIZE = 25
GOOGLE_SEARCH_URL = "https://www.google.com/search?q={query}&num={page_size}&start={start}"
DEFAULT_TIMEOUT_SECONDS = 15

FIND_WINDOW_SCRIPT = """
on run argv
    set markerText to item 1 of argv
    tell application "Google Chrome"
        repeat with browserWindow in windows
            repeat with browserTab in tabs of browserWindow
                try
                    if (URL of browserTab as text) contains markerText then
                        return (id of browserWindow) as text
                    end if
                end try
            end repeat
        end repeat
    end tell
    return ""
end run
"""

OPEN_TAB_SCRIPT = """
on run argv
    set windowId to (item 1 of argv) as integer
    set targetUrl to item 2 of argv
    tell application "Google Chrome"
        set newTab to make new tab at end of tabs of window id windowId with properties {URL:targetUrl}
        return (id of newTab) as text
    end tell
end run
"""

EXECUTE_SCRIPT = """
on run argv
    set windowId to (item 1 of argv) as integer
    set tabId to (item 2 of argv) as integer
    set javascriptCode to item 3 of argv
    tell application "Google Chrome"
        return execute tab id tabId of window id windowId javascript javascriptCode
    end tell
end run
"""

CLOSE_TAB_SCRIPT = """
on run argv
    set windowId to (item 1 of argv) as integer
    set tabId to (item 2 of argv) as integer
    tell application "Google Chrome"
        close tab id tabId of window id windowId
    end tell
end run
"""

READY_STATE_SCRIPT = "JSON.stringify({ready:document.readyState,href:location.href,title:document.title})"

GOOGLE_RESULTS_SCRIPT = r"""
(() => {
  const pageText = (document.body?.innerText || "").toLowerCase();
  const blocked = location.pathname.startsWith("/sorry")
    || pageText.includes("unusual traffic from your computer network")
    || pageText.includes("our systems have detected unusual traffic")
    || pageText.includes("verify you are a human");
  if (blocked) {
    return JSON.stringify({
      blocked: true,
      reason: "Google needs verification in the Hunter Chrome profile.",
      items: []
    });
  }
  const seen = new Set();
  const items = [];
  for (const heading of document.querySelectorAll("a h3")) {
    const anchor = heading.closest("a");
    if (!anchor) continue;
    let url = anchor.href || "";
    try {
      const parsed = new URL(url);
      if (parsed.hostname.endsWith("google.com") && parsed.pathname === "/url") {
        url = parsed.searchParams.get("q") || parsed.searchParams.get("url") || "";
      }
    } catch (_error) {
      continue;
    }
    if (!/^https?:\/\//i.test(url) || seen.has(url)) continue;
    seen.add(url);
    const result = anchor.closest("div.MjjYud")
      || anchor.closest("div[data-snhf]")
      || anchor.parentElement?.parentElement;
    const title = (heading.innerText || heading.textContent || "").trim();
    const resultText = (result?.innerText || "").replace(/\s+/g, " ").trim();
    const snippet = resultText.startsWith(title)
      ? resultText.slice(title.length).trim()
      : resultText;
    items.push({url, title, snippet: snippet.slice(0, 1000)});
    if (items.length >= 10) break;
  }
  return JSON.stringify({blocked: false, reason: "", items});
})()
"""

LINKEDIN_SCROLL_SCRIPT = """
window.scrollTo(0, Math.min(document.body.scrollHeight, window.scrollY + 1800));
"ok";
"""

LINKEDIN_RESULTS_SCRIPT = r"""
(() => {
  const pageText = (document.body?.innerText || "").toLowerCase();
  const blocked = /\/(login|authwall|checkpoint)\b/i.test(location.pathname)
    || pageText.includes("sign in or join linkedin")
    || pageText.includes("let's do a quick security check");
  if (blocked) {
    return JSON.stringify({
      blocked: true,
      reason: "LinkedIn needs sign-in or verification in the Hunter Chrome profile.",
      items: []
    });
  }
  const seen = new Set();
  const items = [];
  for (const anchor of document.querySelectorAll('a[href*="/jobs/view/"]')) {
    let url = anchor.href || "";
    const idMatch = url.match(/\/jobs\/view\/(?:[^/?#]+-)?(\d+)/i);
    if (!idMatch) continue;
    url = `https://www.linkedin.com/jobs/view/${idMatch[1]}`;
    if (seen.has(url)) continue;
    seen.add(url);
    const card = anchor.closest(".job-card-container")
      || anchor.closest(".jobs-search-results__list-item")
      || anchor.closest("li");
    const titleText = anchor.innerText || anchor.textContent || "";
    const rawTitle = (titleText.split(/\n+/).map(value => value.trim()).find(Boolean) || "")
      .replace(/\s+with verification.*$/i, "")
      .trim();
    const company = (
      card?.querySelector(".artdeco-entity-lockup__subtitle")?.textContent
      || card?.querySelector(".job-card-container__primary-description")?.textContent
      || ""
    ).replace(/\s+/g, " ").trim();
    const location = (
      card?.querySelector(".job-card-container__metadata-item")?.textContent
      || card?.querySelector(".artdeco-entity-lockup__caption")?.textContent
      || ""
    ).replace(/\s+/g, " ").trim();
    const cardText = (card?.innerText || "").replace(/\s+/g, " ").trim();
    const title = company && !rawTitle.toLowerCase().includes(company.toLowerCase())
      ? `${rawTitle} at ${company}`
      : rawTitle;
    items.push({
      url,
      title,
      company,
      location,
      snippet: [location, cardText].filter(Boolean).join(" · ").slice(0, 1200)
    });
    if (items.length >= 25) break;
  }
  return JSON.stringify({blocked: false, reason: "", items});
})()
"""

DETAIL_RESULTS_SCRIPT = r"""
(() => {
  const pageText = (document.body?.innerText || "").toLowerCase();
  const blocked = /\/(login|authwall|checkpoint)\b/i.test(location.pathname)
    || pageText.includes("sign in or join linkedin")
    || pageText.includes("let's do a quick security check")
    || pageText.includes("verify you are a human")
    || pageText.includes("unusual traffic from your computer network");
  if (blocked) {
    return JSON.stringify({
      blocked: true,
      reason: "The posting page needs sign-in or verification in the Hunter Chrome profile.",
      items: []
    });
  }

  const text = value => (value || "").replace(/\s+/g, " ").trim();
  const firstText = selectors => {
    for (const selector of selectors) {
      const value = text(document.querySelector(selector)?.textContent || "");
      if (value) return value;
    }
    return "";
  };
  const jobObjects = [];
  for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
    try {
      const decoded = JSON.parse(script.textContent || "{}");
      const pending = Array.isArray(decoded) ? decoded : [decoded];
      for (const item of pending) {
        if (!item || typeof item !== "object") continue;
        jobObjects.push(item);
        if (Array.isArray(item["@graph"])) jobObjects.push(...item["@graph"]);
      }
    } catch (_error) {
      // Ignore malformed structured data and continue with visible page content.
    }
  }
  const job = jobObjects.find(item => {
    const types = Array.isArray(item?.["@type"]) ? item["@type"] : [item?.["@type"]];
    return types.includes("JobPosting");
  }) || {};
  const organization = job.hiringOrganization && typeof job.hiringOrganization === "object"
    ? text(job.hiringOrganization.name || "")
    : "";
  const organizationObjects = [];
  if (job.hiringOrganization && typeof job.hiringOrganization === "object") {
    organizationObjects.push(job.hiringOrganization);
  }
  for (const item of jobObjects) {
    const types = Array.isArray(item?.["@type"]) ? item["@type"] : [item?.["@type"]];
    if (!types.some(type => ["Organization", "Corporation", "LocalBusiness"].includes(type))) continue;
    if (organization && text(item.name || "").toLowerCase() !== organization.toLowerCase()) continue;
    organizationObjects.push(item);
  }
  const structuredIndustry = organizationObjects
    .map(item => item.industry || item.naics || "")
    .flat()
    .map(text)
    .filter(Boolean)
    .join(", ");
  const sizeText = value => {
    if (!value) return "";
    if (typeof value === "object") {
      const minimum = text(value.minValue || "");
      const maximum = text(value.maxValue || "");
      value = minimum && maximum ? `${minimum}–${maximum}` : (value.value || minimum || maximum || "");
    }
    const cleaned = text(String(value)).replace(/\s*(?:employees?|people)\s*$/i, "").trim();
    return /\d/.test(cleaned) ? `${cleaned} employees` : "";
  };
  const structuredSize = organizationObjects
    .map(item => sizeText(item.numberOfEmployees || item.employees))
    .find(Boolean) || "";
  const locationParts = [];
  const locations = Array.isArray(job.jobLocation) ? job.jobLocation : (job.jobLocation ? [job.jobLocation] : []);
  for (const item of locations) {
    const address = item?.address && typeof item.address === "object" ? item.address : item;
    const value = [address?.addressLocality, address?.addressRegion, address?.addressCountry]
      .map(part => typeof part === "object" ? part?.name : part)
      .map(text)
      .filter(Boolean)
      .join(", ");
    if (value) locationParts.push(value);
  }
  const requirements = Array.isArray(job.applicantLocationRequirements)
    ? job.applicantLocationRequirements
    : (job.applicantLocationRequirements ? [job.applicantLocationRequirements] : []);
  for (const item of requirements) {
    const value = text(typeof item === "object" ? item?.name : item);
    if (value) locationParts.push(value);
  }
  if (text(job.jobLocationType || "").toUpperCase() === "TELECOMMUTE") locationParts.unshift("Remote");

  const title = text(job.title || "") || firstText([
    ".job-details-jobs-unified-top-card__job-title",
    ".top-card-layout__title",
    "[data-automation-id='jobPostingHeader']",
    "h1"
  ]);
  const company = organization || firstText([
    ".job-details-jobs-unified-top-card__company-name",
    ".topcard__org-name-link",
    "[data-automation-id='company']"
  ]);
  const visibleLocation = firstText([
    ".job-details-jobs-unified-top-card__primary-description-container",
    ".topcard__flavor--bullet",
    "[data-automation-id='locations']",
    "[data-automation-id='location']"
  ]);
  const description = text(job.description || "") || firstText([
    ".jobs-description__content",
    ".jobs-description-content__text",
    "#job-details",
    "[data-automation-id='jobPostingDescription']",
    "main"
  ]);
  const topCompanyLink = document.querySelector(
    ".job-details-jobs-unified-top-card__company-name a[href*='/company/'], "
    + ".topcard__org-name-link[href*='/company/']"
  );
  const companyCard = document.querySelector(".jobs-company__box")
    || document.querySelector(".jobs-company__card")
    || document.querySelector("[data-view-name='job-details-about-company']")
    || topCompanyLink?.closest("section");
  const companyLines = (companyCard?.innerText || "")
    .split(/\n+/)
    .map(text)
    .filter(Boolean);
  const employeeLineIndex = companyLines.findIndex(line => (
    /\b\d[\d,\s]*(?:[-–]\s*\d[\d,\s]*|\+)?\s+employees?\b/i.test(line)
  ));
  const visibleSize = employeeLineIndex >= 0
    ? sizeText(companyLines[employeeLineIndex].match(/\b\d[\d,\s]*(?:[-–]\s*\d[\d,\s]*|\+)?\s+employees?\b/i)?.[0] || "")
    : "";
  const visibleIndustry = employeeLineIndex > 0
    ? companyLines.slice(0, employeeLineIndex).reverse().find(line => (
      line.toLowerCase() !== organization.toLowerCase()
      && line.length <= 100
      && !/\b(?:followers?|follow|about|employees?|jobs?)\b/i.test(line)
    )) || ""
    : "";
  const organizationProfile = organizationObjects
    .map(item => Array.isArray(item.sameAs) ? item.sameAs[0] : (item.sameAs || item.url || ""))
    .map(text)
    .find(Boolean) || "";
  const companyProfile = companyCard?.querySelector('a[href*="/company/"]')?.href
    || topCompanyLink?.href
    || organizationProfile;
  const canonical = document.querySelector('link[rel="canonical"]')?.href || location.href;
  const outboundApply = Array.from(document.querySelectorAll("a[href]")).find(anchor => {
    const href = anchor.href || "";
    const label = text(anchor.textContent || "").toLowerCase();
    try {
      const host = new URL(href).hostname.replace(/^www\./, "");
      return label.includes("apply") && host && host !== "linkedin.com" && !host.endsWith(".linkedin.com");
    } catch (_error) {
      return false;
    }
  });
  const isLinkedIn = location.hostname === "linkedin.com" || location.hostname.endsWith(".linkedin.com");
  const item = {
    url: location.href,
    canonical_url: isLinkedIn ? (outboundApply?.href || "") : canonical,
    title,
    company,
    location: [...new Set([...locationParts, visibleLocation].filter(Boolean))].join("; "),
    company_industry: structuredIndustry || visibleIndustry,
    company_size: structuredSize || visibleSize,
    company_profile_url: companyProfile,
    company_metadata_source: (structuredIndustry || visibleIndustry || structuredSize || visibleSize || companyProfile)
      ? location.href
      : "",
    description_text: description.slice(0, 80000)
  };
  return JSON.stringify({blocked: false, reason: "", items: [item]});
})()
"""

COMPANY_RESULTS_SCRIPT = r"""
(() => {
  const pageText = (document.body?.innerText || "").toLowerCase();
  const blocked = /\/(login|authwall|checkpoint)\b/i.test(location.pathname)
    || pageText.includes("sign in or join linkedin")
    || pageText.includes("let's do a quick security check")
    || pageText.includes("verify you are a human")
    || pageText.includes("unusual traffic from your computer network");
  if (blocked) {
    return JSON.stringify({
      blocked: true,
      reason: "The company page needs sign-in or verification in the Hunter Chrome profile.",
      items: []
    });
  }

  const text = value => (value || "").replace(/\s+/g, " ").trim();
  const objects = [];
  for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
    try {
      const decoded = JSON.parse(script.textContent || "{}");
      const pending = Array.isArray(decoded) ? decoded : [decoded];
      for (const item of pending) {
        if (!item || typeof item !== "object") continue;
        objects.push(item);
        if (Array.isArray(item["@graph"])) objects.push(...item["@graph"]);
      }
    } catch (_error) {
      // Visible company details remain available when structured data is malformed.
    }
  }
  const organization = objects.find(item => {
    const types = Array.isArray(item?.["@type"]) ? item["@type"] : [item?.["@type"]];
    return types.some(type => ["Organization", "Corporation", "LocalBusiness"].includes(type));
  }) || {};
  const labeledValue = labels => {
    const wanted = labels.map(label => label.toLowerCase());
    for (const term of document.querySelectorAll("dt")) {
      if (!wanted.includes(text(term.textContent || "").toLowerCase())) continue;
      const value = text(term.nextElementSibling?.textContent || "");
      if (value) return value;
    }
    const lines = (document.body?.innerText || "").split(/\n+/).map(text).filter(Boolean);
    for (let index = 0; index < lines.length - 1; index += 1) {
      if (wanted.includes(lines[index].toLowerCase())) return lines[index + 1];
    }
    return "";
  };
  const sizeText = value => {
    if (!value) return "";
    if (typeof value === "object") {
      const minimum = text(value.minValue || "");
      const maximum = text(value.maxValue || "");
      value = minimum && maximum ? `${minimum}–${maximum}` : (value.value || minimum || maximum || "");
    }
    const cleaned = text(String(value)).replace(/\s*(?:employees?|people)\s*$/i, "").trim();
    return /\d/.test(cleaned) ? `${cleaned} employees` : "";
  };
  const sameAs = Array.isArray(organization.sameAs) ? organization.sameAs : [organization.sameAs];
  const websiteValue = labeledValue(["Website"]);
  const websiteLink = Array.from(document.querySelectorAll("a[href]")).find(anchor => (
    text(anchor.textContent || "").toLowerCase() === websiteValue.toLowerCase()
    || text(anchor.getAttribute("aria-label") || "").toLowerCase().includes("website")
  ));
  const name = text(organization.name || "")
    || text(document.querySelector("h1")?.textContent || "");
  const industry = text(organization.industry || organization.naics || "")
    || labeledValue(["Industry"]);
  const companySize = sizeText(organization.numberOfEmployees || organization.employees)
    || sizeText(labeledValue(["Company size", "Company Size"]));
  const outboundUrl = value => {
    try {
      const parsed = new URL(value || "", location.href);
      if (parsed.hostname.endsWith("linkedin.com") && parsed.pathname.includes("/redir/redirect")) {
        return parsed.searchParams.get("url") || "";
      }
      return parsed.hostname.endsWith("linkedin.com") ? "" : parsed.href;
    } catch (_error) {
      return "";
    }
  };
  const website = outboundUrl(websiteLink?.href || "")
    || (/^https?:\/\//i.test(websiteValue) ? websiteValue : "")
    || outboundUrl(text(organization.url || ""));
  const profileUrl = document.querySelector('link[rel="canonical"]')?.href
    || sameAs.map(text).find(value => value.includes("/company/"))
    || location.href;
  return JSON.stringify({
    blocked: false,
    reason: "",
    items: [{
      company: name,
      company_industry: industry,
      company_size: companySize,
      company_profile_url: profileUrl,
      company_metadata_source: location.href,
      website
    }]
  });
})()
"""


class BrowserDiscoveryError(RuntimeError):
    """Raised when the dedicated Hunter Chrome bridge is unavailable."""


class HunterChrome:
    def __init__(
        self,
        runner=None,
        sleeper=None,
        timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
        min_delay_seconds=1.25,
        max_delay_seconds=2.25,
        randomizer=None,
    ):
        self.runner = runner or subprocess.run
        self.sleeper = sleeper or time.sleep
        self.timeout_seconds = timeout_seconds
        self.min_delay_seconds = min_delay_seconds
        self.max_delay_seconds = max_delay_seconds
        self.randomizer = randomizer or random.uniform
        self.window_id = ""
        self.last_search_at = 0.0

    def _run(self, script, *arguments):
        try:
            completed = self.runner(
                [OSASCRIPT_PATH, "-e", script, *[str(argument) for argument in arguments]],
                capture_output=True,
                close_fds=False,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise BrowserDiscoveryError("Hunter browser search requires macOS and Google Chrome.") from exc
        except subprocess.TimeoutExpired as exc:
            raise BrowserDiscoveryError("Hunter Chrome did not respond before the search timed out.") from exc
        if completed.returncode:
            detail = (completed.stderr or completed.stdout or "").strip()
            if "Executing JavaScript through AppleScript is turned off" in detail:
                raise BrowserDiscoveryError(
                    "In Hunter Chrome, enable View > Developer > Allow JavaScript from Apple Events."
                )
            raise BrowserDiscoveryError(f"Hunter Chrome could not complete the search. {detail}".strip())
        return (completed.stdout or "").strip()

    def find_window(self):
        self.window_id = self._run(FIND_WINDOW_SCRIPT, BRIDGE_MARKER)
        if not self.window_id:
            raise BrowserDiscoveryError(
                "Open the Hunter bridge tab in the dedicated Hunter Chrome profile, then try again."
            )
        return self.window_id

    def _open_tab(self, url):
        if not self.window_id:
            self.find_window()
        tab_id = self._run(OPEN_TAB_SCRIPT, self.window_id, url)
        if not tab_id:
            raise BrowserDiscoveryError("Hunter Chrome did not create a search tab.")
        return tab_id

    def _execute(self, tab_id, javascript):
        return self._run(EXECUTE_SCRIPT, self.window_id, tab_id, javascript)

    def _close_tab(self, tab_id):
        try:
            self._run(CLOSE_TAB_SCRIPT, self.window_id, tab_id)
        except BrowserDiscoveryError:
            pass

    def _wait_until_ready(self, tab_id):
        deadline = time.monotonic() + self.timeout_seconds
        last_state = {}
        while time.monotonic() < deadline:
            raw = self._execute(tab_id, READY_STATE_SCRIPT)
            try:
                last_state = json.loads(raw or "{}")
            except json.JSONDecodeError:
                last_state = {}
            if last_state.get("ready") in {"interactive", "complete"}:
                return last_state
            self.sleeper(0.25)
        title = last_state.get("title", "")
        raise BrowserDiscoveryError(f"Hunter Chrome did not finish loading the search page. {title}".strip())

    def _search_tab(self, url, extraction_script, scroll=False):
        if self.last_search_at:
            target_delay = self.randomizer(self.min_delay_seconds, self.max_delay_seconds)
            elapsed = time.monotonic() - self.last_search_at
            if elapsed < target_delay:
                self.sleeper(target_delay - elapsed)
        self.last_search_at = time.monotonic()
        tab_id = self._open_tab(url)
        try:
            self._wait_until_ready(tab_id)
            if scroll:
                for _index in range(2):
                    self._execute(tab_id, LINKEDIN_SCROLL_SCRIPT)
                    self.sleeper(0.8)
            raw = self._execute(tab_id, extraction_script)
            try:
                payload = json.loads(raw or "{}")
            except json.JSONDecodeError as exc:
                raise BrowserDiscoveryError("Hunter Chrome returned an unreadable search result.") from exc
            if payload.get("blocked"):
                raise BrowserDiscoveryError(payload.get("reason") or "The search site needs attention in Hunter Chrome.")
            return payload.get("items", [])
        finally:
            self._close_tab(tab_id)

    def google(self, query, page=0):
        url = GOOGLE_SEARCH_URL.format(
            query=quote_plus(query),
            page_size=GOOGLE_PAGE_SIZE,
            start=max(0, int(page)) * GOOGLE_PAGE_SIZE,
        )
        return self._search_tab(url, GOOGLE_RESULTS_SCRIPT)

    def linkedin(self, url, page=0):
        parsed = urlparse(url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["start"] = str(max(0, int(page)) * LINKEDIN_PAGE_SIZE)
        paged_url = urlunparse(parsed._replace(query=urlencode(query)))
        return self._search_tab(paged_url, LINKEDIN_RESULTS_SCRIPT, scroll=True)

    def details(self, url):
        items = self._search_tab(url, DETAIL_RESULTS_SCRIPT, scroll=True)
        return items[0] if items else {}

    def company(self, name, profile_url=""):
        target_url = (profile_url or "").strip()
        if not target_url:
            results = self.google(f'site:linkedin.com/company "{name}"')
            target_url = next(
                (
                    item.get("url", "")
                    for item in results
                    if "linkedin.com" in urlparse(item.get("url", "")).netloc.lower()
                    and "/company/" in urlparse(item.get("url", "")).path.lower()
                ),
                "",
            )
        if not target_url:
            return {}
        parsed = urlparse(target_url)
        if "linkedin.com" in parsed.netloc.lower() and "/company/" in parsed.path.lower():
            segments = [segment for segment in parsed.path.split("/") if segment]
            try:
                company_index = segments.index("company")
            except ValueError:
                company_index = -1
            if company_index >= 0 and len(segments) > company_index + 1:
                company_path = f"/company/{segments[company_index + 1]}/about/"
                target_url = urlunparse(parsed._replace(path=company_path, query="", fragment=""))
        items = self._search_tab(target_url, COMPANY_RESULTS_SCRIPT, scroll=True)
        return items[0] if items else {}


def search(engine, value, page=0, browser=None):
    chrome = browser or HunterChrome()
    chrome.find_window()
    if engine == "linkedin":
        return chrome.linkedin(value, page=page)
    if engine == "google":
        return chrome.google(value, page=page)
    raise ValueError(f"Unsupported Hunter Chrome search engine: {engine}")
