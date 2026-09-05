import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { BriefcaseIcon, ExternalIcon, FilterIcon, RefreshIcon, SearchIcon, XIcon } from "../components/Icons";
import { dateOnlyLabel, titleCase } from "../core/format";
import { routes } from "../core/routes";
import type {
  Application,
  Company,
  DiscoveryCandidate,
  DiscoveryCandidateDetail,
  DiscoveryCandidateDetails,
  DiscoveryLastRunSummary,
  DiscoverySearch,
  DiscoverySearchLaneDefinition,
  DiscoverySearchUpdates
} from "../core/types";
import { DISMISSED_DISCOVERY_RUN_KEY, DiscoveryFilter, ROLE_FAMILY_OPTIONS, WORK_MODE_OPTIONS } from './discoveryConfig';

export function FitBrief({ candidate, company }: { candidate: DiscoveryCandidateDetail; company?: Company }) {
  return (
    <section className="discovery-fit-brief" aria-label="Fit brief">
      <div className="discovery-signal-strip" aria-label="Role quality signals">
        <div><span>Match</span><strong>{candidate.detail_state === "ready" ? candidate.fit_score || "—" : "Needs review"}</strong></div>
        <div><span>Role family</span><strong>{candidate.role_family || "Unclassified"}</strong></div>
        <div><span>Source</span><strong>{candidate.source_trust_label}</strong></div>
        <div><span>Freshness</span><strong>{freshnessShortLabel(candidate)}</strong></div>
      </div>
      {candidate.review_state !== "ready" ? (
        <div className="discovery-review-warning" role="note">
          <strong>{processingLabel(candidate)}</strong>
          <span>{candidate.detail_last_error || candidate.review_next_action || "Confirm the posting details before adding it to Considering."}</span>
        </div>
      ) : null}
      <div className="discovery-fit-columns">
        <div>
          <span className="eyebrow">Why it fits</span>
          {(candidate.fit_strengths || []).length
            ? <ul>{candidate.fit_strengths.map(item => <li key={item}>{item}</li>)}</ul>
            : <p>No supported fit strengths yet.</p>}
        </div>
      </div>
      {company ? <span className="discovery-fit-company">{company.industry || "Industry unknown"} · {company.company_size || "Size unknown"}</span> : null}
    </section>
  );
}

export function ReviewSummarySection({ title, items, empty }: { title: string; items: string[]; empty: string }) {
  return (
    <section>
      <span className="eyebrow">{title}</span>
      {items.length ? <ul>{items.map(item => <li key={item}>{item}</li>)}</ul> : <p>{empty}</p>}
    </section>
  );
}

export function candidateReviewSummary(candidate: DiscoveryCandidate) {
  const text = candidate.description_text || "";
  const responsibilities = extractPostingSection(text, ["responsibilities", "what you will do", "what you'll do", "the role"]);
  const requirements = extractPostingSection(text, ["requirements", "qualifications", "what we are looking for", "what we're looking for"]);
  const compensation = extractPostingSentences(text, /\$\s?\d|salary|compensation|base pay|pay range/i, 2);
  const responsibilityFallback = extractPostingSentences(text, /\b(lead|manage|own|drive|partner|coordinate|deliver|build|develop|oversee)\b/i, 3);
  const requirementFallback = extractPostingSentences(text, /\b(require|experience|years|degree|proficien|ability to)\b/i, 3);
  const concerns = uniqueValues([
    ...(candidate.fit_gaps || []),
    ...(candidate.detail_gaps || []).map(gap => gap.label),
    ...String(candidate.warnings || "").split(/\n+/),
    candidate.freshness_status === "needs-review" ? "Posting freshness needs confirmation" : "",
    !candidate.is_direct_employer_source ? "Direct employer posting link is missing" : ""
  ]).slice(0, 5);
  return {
    responsibilities: (responsibilities.length ? responsibilities : responsibilityFallback).slice(0, 4),
    requirements: (requirements.length ? requirements : requirementFallback).slice(0, 4),
    compensation,
    concerns
  };
}

export function extractPostingSection(text: string, headings: string[]) {
  const lines = postingLines(text);
  const headingIndex = lines.findIndex(line => headings.some(heading => line.toLowerCase().replace(/[:\s]+$/, "") === heading));
  if (headingIndex < 0) return [];
  const items: string[] = [];
  for (const line of lines.slice(headingIndex + 1)) {
    if (items.length && /^[A-Z][A-Za-z &/]{2,35}:?$/.test(line)) break;
    if (usablePostingSummaryLine(line)) items.push(line.replace(/^[-•*]\s*/, ""));
    if (items.length >= 4) break;
  }
  return uniqueValuesInOrder(items);
}

export function extractPostingSentences(text: string, pattern: RegExp, limit: number) {
  const candidates = postingLines(text).flatMap(line => line.split(/(?<=[.!?])\s+/));
  return uniqueValuesInOrder(candidates.filter(sentence => pattern.test(sentence) && usablePostingSummaryLine(sentence))).slice(0, limit);
}

export function postingLines(text: string) {
  return String(text || "")
    .replace(/\r/g, "")
    .split(/\n+/)
    .map(line => line.replace(/\s+/g, " ").trim())
    .filter(Boolean);
}

export function usablePostingSummaryLine(value: string) {
  const line = value.replace(/^[-•*]\s*/, "").trim();
  if (line.length < 28 || line.length > 260) return false;
  if (/apply|save|show match|create cover letter|promoted by hirer|applicants/i.test(line)) return false;
  return line.split(/\s+/).length >= 5;
}

export function CandidateReviewModal({
  candidate,
  company,
  applications,
  index,
  total,
  pending,
  close,
  previous,
  next,
  edit,
  ignore,
  markCompanyNotInterested,
  markDuplicate,
  refresh,
  consider
}: {
  candidate: DiscoveryCandidateDetail;
  company?: Company;
  applications: Application[];
  index: number;
  total: number;
  pending: boolean;
  close: () => void;
  previous: () => void;
  next: () => void;
  edit: () => void;
  ignore: () => void;
  markCompanyNotInterested: () => void;
  markDuplicate: (applicationId: string) => void;
  refresh: () => void;
  consider: () => void;
}) {
  const [duplicateOpen, setDuplicateOpen] = useState(false);
  const [decision, setDecision] = useState<"ignored" | "pursued" | "">("");
  const summary = useMemo(() => candidateReviewSummary(candidate), [candidate]);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape" && duplicateOpen) {
        setDuplicateOpen(false);
        return;
      }
      if (event.key === "Escape") close();
      if (duplicateOpen) return;
      if (
        event.target instanceof HTMLInputElement
        || event.target instanceof HTMLTextAreaElement
        || event.target instanceof HTMLSelectElement
      ) return;
      if (event.key === "ArrowLeft") previous();
      if (event.key === "ArrowRight") next();
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [close, duplicateOpen, next, previous]);

  return (
    <div className="modal-backdrop">
      <article className="modal discovery-review-modal" role="dialog" aria-modal="true" aria-labelledby="discovery-review-title">
        <div className="modal-header">
          <div>
            <span className="eyebrow">Review {index + 1} of {total}</span>
            <h2 id="discovery-review-title">{candidate.title || "Role details needed"}</h2>
            <p>{company?.name || "Company needed"}</p>
          </div>
          <button className="button compact" type="button" onClick={close}><XIcon size={18} /> Close</button>
        </div>
        <FitBrief candidate={candidate} company={company} />
        <div className="discovery-review-summary-grid">
          <ReviewSummarySection title="What you would do" items={summary.responsibilities} empty="Responsibilities were not captured clearly; open the posting to confirm." />
          <ReviewSummarySection title="Key requirements" items={summary.requirements} empty="Requirements were not captured clearly; open the posting to confirm." />
          <ReviewSummarySection title="Compensation" items={summary.compensation} empty="Not listed in the captured posting." />
          <ReviewSummarySection title="Location and work mode" items={[candidateLocationLabel(candidate)]} empty="Location not captured." />
          <ReviewSummarySection title="Concerns" items={summary.concerns} empty="No additional concerns identified." />
        </div>
        <div className="discovery-review-source-summary">
          <span>{freshnessLabel(candidate)} · {candidate.source_trust_label} source</span>
          {(candidate.source_urls || []).length > 1 ? (
            <details><summary>{candidate.source_urls.length} source links</summary><ul>{candidate.source_urls.map(url => <li key={url}><a href={url} target="_blank" rel="noreferrer">{sourceLabel(url)}</a></li>)}</ul></details>
          ) : null}
        </div>
        {duplicateOpen ? (
          <DuplicatePostingPicker
            candidate={candidate}
            company={company}
            applications={applications}
            pending={pending}
            cancel={() => setDuplicateOpen(false)}
            confirm={markDuplicate}
          />
        ) : null}
        <div className="discovery-review-actions">
          <div>
            <button className="button" type="button" disabled={index <= 0 || pending} onClick={previous}>Previous</button>
            <button className="button" type="button" disabled={total <= 1 || pending} onClick={next}>Next</button>
          </div>
          <div>
            <a className="button" href={candidate.canonical_url || candidate.url} target="_blank" rel="noreferrer"><ExternalIcon size={15} /> Open posting</a>
            <button className="button" type="button" disabled={pending} onClick={edit}>Edit details</button>
            <button className="button" type="button" disabled={pending} onClick={() => { setDecision("ignored"); ignore(); }}>{pending && decision === "ignored" ? "Saving…" : "Ignore"}</button>
            <button
              className="button company-not-interested"
              type="button"
              disabled={pending || !company}
              onClick={markCompanyNotInterested}
              title={company
                ? `Hide current and future Discovery roles from ${company.name}`
                : "Associate a company before marking it Not interested"}
            >
              Not interested in company
            </button>
            <button className="button" type="button" disabled={pending || !applications.length} onClick={() => setDuplicateOpen(true)}>
              Mark duplicate
            </button>
            {candidate.review_state === "needs-freshness" || candidate.review_state === "needs-detail" || candidate.review_state === "needs-qualification" ? (
              <button className="button primary" type="button" disabled={pending} onClick={refresh}>
                <RefreshIcon size={15} /> {pending
                  ? "Checking…"
                  : candidate.freshness_status === "needs-review"
                    ? "Check again"
                    : candidate.review_state === "needs-qualification" ? "Verify location" : candidate.review_state === "needs-freshness" ? "Check posting" : "Resolve details"}
              </button>
            ) : (
              <button className="button primary" type="button" disabled={pending || candidate.review_state !== "ready"} onClick={() => { setDecision("pursued"); consider(); }}>{pending && decision === "pursued" ? "Saving…" : "Consider"}</button>
            )}
          </div>
        </div>
      </article>
    </div>
  );
}

export function DuplicatePostingPicker({
  candidate,
  company,
  applications,
  pending,
  cancel,
  confirm
}: {
  candidate: DiscoveryCandidate;
  company?: Company;
  applications: Application[];
  pending: boolean;
  cancel: () => void;
  confirm: (applicationId: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [selectedApplicationId, setSelectedApplicationId] = useState("");
  const matches = useMemo(() => {
    const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    return [...applications]
      .filter(application => {
        if (!terms.length) return true;
        const searchable = postingSearchText(application);
        return terms.every(term => searchable.includes(term));
      })
      .sort((left, right) => (
        duplicatePostingScore(right, candidate, company) - duplicatePostingScore(left, candidate, company)
        || Number(right.is_active) - Number(left.is_active)
        || (right.date_found || "").localeCompare(left.date_found || "")
        || right.id.localeCompare(left.id)
      ))
      .slice(0, 20);
  }, [applications, candidate, company, query]);

  return (
    <section className="discovery-duplicate-picker" aria-label="Associate duplicate with an existing posting">
      <div className="discovery-duplicate-heading">
        <div>
          <strong>Choose the posting this duplicates</strong>
          <span>Hunter will keep the Discovery result linked to the posting you select.</span>
        </div>
        <button className="icon-button" type="button" onClick={cancel} aria-label="Cancel duplicate association"><XIcon size={15} /></button>
      </div>
      <label className="search discovery-duplicate-search">
        <span className="sr-only">Search existing postings</span>
        <SearchIcon />
        <input
          autoFocus
          value={query}
          onChange={event => setQuery(event.target.value)}
          type="search"
          placeholder="Search by company, role, location, or posting ID..."
        />
      </label>
      <div className="discovery-duplicate-results" role="list" aria-label="Existing postings">
        {matches.map(application => (
          <button
            className={selectedApplicationId === application.id ? "discovery-duplicate-option selected" : "discovery-duplicate-option"}
            key={application.id}
            type="button"
            onClick={() => setSelectedApplicationId(application.id)}
            aria-pressed={selectedApplicationId === application.id}
          >
            <span>
              <strong>{application.role}</strong>
              <small>{application.company} · {application.location || "Location unknown"} · {postingStageLabel(application)}</small>
            </span>
            <span>{application.id}</span>
          </button>
        ))}
        {!matches.length ? <p>No existing postings match that search.</p> : null}
      </div>
      <div className="discovery-duplicate-actions">
        <button className="button" type="button" disabled={pending} onClick={cancel}>Cancel</button>
        <button
          className="button primary"
          type="button"
          disabled={pending || !selectedApplicationId}
          onClick={() => confirm(selectedApplicationId)}
        >
          <BriefcaseIcon size={15} /> Associate duplicate
        </button>
      </div>
    </section>
  );
}

export function DiscoveryMultiFilter({
  label,
  options,
  selected,
  onChange
}: {
  label: string;
  options: Array<{ id: string; label: string }>;
  selected: string[];
  onChange: (values: string[]) => void;
}) {
  const allSelected = options.length === selected.length;
  const selectedLabel = options.find(option => option.id === selected[0])?.label;
  const summary = allSelected ? "All" : selected.length === 1 ? selectedLabel || "1 selected" : `${selected.length} selected`;

  function toggle(value: string) {
    onChange(selected.includes(value) ? selected.filter(item => item !== value) : [...selected, value]);
  }

  return (
    <details className="filter multi-filter">
      <summary>{label} <span>{summary}</span></summary>
      <div className="multi-filter-menu">
        <label className="multi-filter-option">
          <input checked={allSelected} onChange={event => onChange(event.target.checked ? options.map(option => option.id) : [])} type="checkbox" />
          All
        </label>
        {options.map(option => (
          <label className="multi-filter-option" key={option.id}>
            <input checked={selected.includes(option.id)} onChange={() => toggle(option.id)} type="checkbox" />
            {option.label}
          </label>
        ))}
      </div>
    </details>
  );
}

export function postingSearchText(application: Application) {
  return [
    application.id,
    application.company,
    application.role,
    application.location,
    application.work_mode,
    application.stage,
    application.outcome,
    application.source_url
  ].join(" ").toLowerCase();
}

export function duplicatePostingScore(
  application: Application,
  candidate: DiscoveryCandidate,
  company?: Company
) {
  let score = 0;
  const candidateUrls = new Set(
    [candidate.url, candidate.canonical_url, ...(candidate.source_urls || [])]
      .map(normalizedPostingUrl)
      .filter(Boolean)
  );
  if (candidateUrls.has(normalizedPostingUrl(application.source_url))) score += 1000;
  if (candidate.company_id && application.company_id === candidate.company_id) score += 300;
  if (company?.name && application.company.toLowerCase() === company.name.toLowerCase()) score += 200;

  const candidateTitle = candidate.title.trim().toLowerCase();
  const applicationTitle = application.role.trim().toLowerCase();
  if (candidateTitle && candidateTitle === applicationTitle) score += 400;
  const candidateTerms = new Set(candidateTitle.match(/[a-z0-9]+/g) || []);
  const applicationTerms = new Set(applicationTitle.match(/[a-z0-9]+/g) || []);
  score += [...candidateTerms].filter(term => applicationTerms.has(term)).length * 12;
  return score;
}

export function normalizedPostingUrl(value: string) {
  return value.trim().toLowerCase().replace(/\/+$/, "");
}

export function postingStageLabel(application: Application) {
  if (application.stage === "closed" && application.outcome) {
    return `${titleCase(application.stage)} · ${titleCase(application.outcome)}`;
  }
  return titleCase(application.stage || "tracked");
}

export function CandidateDetailsModal({
  candidate,
  companies,
  pending,
  close,
  save
}: {
  candidate: DiscoveryCandidateDetail;
  companies: Company[];
  pending: boolean;
  close: () => void;
  save: (updates: DiscoveryCandidateDetails) => Promise<void>;
}) {
  const initialCompany = companies.find(company => company.id === candidate.company_id);
  const [draft, setDraft] = useState<DiscoveryCandidateDetails>({
    company_id: candidate.company_id,
    company_name: initialCompany?.name || candidate.company || "",
    title: candidate.title,
    canonical_url: candidate.canonical_url,
    location: candidate.location,
    work_mode: candidate.work_mode,
    description_text: candidate.description_text,
    notes: candidate.notes
  });
  const [descriptionEditorOpen, setDescriptionEditorOpen] = useState(
    () => !usableDiscoveryDescription(candidate.description_text)
  );
  const companyName = draft.company_name || "";
  const detailsRequirements = candidateDetailRequirements(draft);
  const descriptionRequired = !usableDiscoveryDescription(draft.description_text || "");
  const linkedCompany = companies.find(company => company.id === draft.company_id)
    || companies.find(company => normalizedCompanyName(company.name) === normalizedCompanyName(companyName));
  const companyOptions = useMemo(
    () => [...companies].sort((left, right) => left.name.localeCompare(right.name)),
    [companies]
  );

  function updateCompanyName(value: string) {
    const match = companies.find(company => normalizedCompanyName(company.name) === normalizedCompanyName(value));
    setDraft(current => ({
      ...current,
      company_id: match?.id || "",
      company_name: value
    }));
  }

  return (
    <div className="modal-backdrop">
      <article className="modal discovery-details-modal" role="dialog" aria-modal="true" aria-labelledby="discovery-details-title">
        <div className="modal-header">
          <h2 id="discovery-details-title">{candidate.title || "Complete role details"}</h2>
          <button className="button compact" type="button" onClick={close}><XIcon size={18} /> Close</button>
        </div>
        <FitBrief candidate={candidate} company={linkedCompany} />
        <form className="management-form" onSubmit={event => { event.preventDefault(); void save(draft); }}>
          <label className="form-field">
            Company
            <input
              required
              list="discovery-company-options"
              value={companyName}
              onChange={event => updateCompanyName(event.target.value)}
              placeholder="Select or enter a company"
              autoComplete="off"
            />
            <datalist id="discovery-company-options">
              {companyOptions.map(company => <option key={company.id} value={company.name} />)}
            </datalist>
            <small>Select an existing company or type a new company name.</small>
          </label>
          <label className="form-field">Role title <input required value={draft.title || ""} onChange={event => setDraft({ ...draft, title: event.target.value })} /></label>
          <label className="form-field">Location <input value={draft.location || ""} onChange={event => setDraft({ ...draft, location: event.target.value })} /></label>
          <label className="form-field">Work mode <input value={draft.work_mode || ""} onChange={event => setDraft({ ...draft, work_mode: event.target.value })} placeholder="Remote, Hybrid, or On-site" /></label>
          {detailsRequirements.length ? (
            <div className="form-field full discovery-details-requirements" role="note">
              <strong>Still needed before Hunter can verify this role</strong>
              <ul>{detailsRequirements.map(requirement => <li key={requirement}>{requirement}</li>)}</ul>
            </div>
          ) : candidate.detail_state !== "ready" ? (
            <div className="form-field full discovery-details-requirements complete" role="note">
              <strong>Ready to verify</strong>
              <span>Save these details to rescore the role.</span>
            </div>
          ) : null}
          {linkedCompany ? (
            <div className="form-field full discovery-company-source">
              <span>{[linkedCompany.industry, linkedCompany.company_size].filter(Boolean).join(" · ") || "Company details have not been researched yet."}</span>
              <Link to={routes.companyDetail(linkedCompany.id)}>View or research company details</Link>
            </div>
          ) : companyName.trim() ? (
            <div className="form-field full discovery-company-source new-company">
              <span>Hunter will add “{companyName.trim()}” as a discovered company when you save.</span>
            </div>
          ) : null}
          <label className="form-field full">Employer posting URL <input type="url" value={draft.canonical_url || ""} onChange={event => setDraft({ ...draft, canonical_url: event.target.value })} /></label>
          <details
            className="form-field full discovery-description-editor"
            open={descriptionEditorOpen}
            onToggle={event => setDescriptionEditorOpen(event.currentTarget.open)}
          >
            <summary>
              Posting description
              {descriptionRequired ? <span className="discovery-description-required">Required</span> : null}
            </summary>
            <textarea className="discovery-description-input" value={draft.description_text || ""} onChange={event => setDraft({ ...draft, description_text: event.target.value })} />
            <small>
              {(draft.description_text || "").trim().length.toLocaleString()} characters
              {descriptionRequired ? " · Add at least 500 characters of actual posting content, not a redirect or search-page response." : " · Complete enough for fit scoring."}
            </small>
          </details>
          <label className="form-field full">Notes <textarea value={draft.notes || ""} onChange={event => setDraft({ ...draft, notes: event.target.value })} /></label>
          <div className="detail-actions form-field full">
            <button className="button primary" type="submit" disabled={pending}><FilterIcon size={15} /> Save and rescore</button>
            <button className="button" type="button" onClick={close}>Cancel</button>
          </div>
        </form>
      </article>
    </div>
  );
}

export function normalizedCompanyName(value: string) {
  return value.trim().toLocaleLowerCase();
}

export function usableDiscoveryDescription(value: string) {
  const description = value.trim();
  if (description.length < 500) return false;
  if (!description.startsWith("{")) return true;
  try {
    const payload = JSON.parse(description) as Record<string, unknown>;
    return payload.widget !== "redirect" && !payload.externalSpa;
  } catch {
    return true;
  }
}

export function candidateDetailRequirements(details: DiscoveryCandidateDetails) {
  const requirements: string[] = [];
  if (!details.company_id && !(details.company_name || "").trim()) {
    requirements.push("Select or add a company.");
  }
  if (!(details.title || "").trim()) {
    requirements.push("Add the role title.");
  }
  if (!usableDiscoveryDescription(details.description_text || "")) {
    requirements.push("Replace the captured response with the complete posting description.");
  }
  if (!(details.location || "").trim() && (details.work_mode || "").trim().toLocaleLowerCase() !== "remote") {
    requirements.push("Add a location, or set the work mode to Remote.");
  }
  return requirements;
}

export function searchUpdates(search: DiscoverySearch): DiscoverySearchUpdates {
  return {
    name: search.name,
    keywords: search.keywords,
    role_family_ids: [...(search.role_family_ids || [])],
    lanes: search.lanes.map(lane => ({ ...lane, work_modes: [...lane.work_modes] })),
    excluded_terms: [...(search.excluded_terms || [])]
  };
}

export function newSearchLane(index: number): DiscoverySearchLaneDefinition {
  return {
    id: `lane-${Date.now()}-${index}`,
    label: "",
    location: "",
    work_modes: ["on-site", "hybrid", "remote"]
  };
}

export function newSearchDraft(): DiscoverySearchUpdates {
  return {
    name: "",
    keywords: "",
    role_family_ids: ROLE_FAMILY_OPTIONS.map(option => option.id),
    lanes: [newSearchLane(0)],
    excluded_terms: []
  };
}

export function updateSearchLane(
  index: number,
  updates: Partial<DiscoverySearchLaneDefinition>,
  draft: DiscoverySearchUpdates,
  setDraft: (next: DiscoverySearchUpdates) => void
) {
  setDraft({
    ...draft,
    lanes: draft.lanes.map((lane, laneIndex) => laneIndex === index ? { ...lane, ...updates } : lane)
  });
}

export function toggleLaneWorkMode(
  index: number,
  workMode: DiscoverySearchLaneDefinition["work_modes"][number],
  draft: DiscoverySearchUpdates,
  setDraft: (next: DiscoverySearchUpdates) => void
) {
  const lane = draft.lanes[index];
  const workModes = lane.work_modes.includes(workMode)
    ? lane.work_modes.filter(value => value !== workMode)
    : [...lane.work_modes, workMode];
  updateSearchLane(index, { work_modes: workModes }, draft, setDraft);
}

export function laneSummary(lane: DiscoverySearchLaneDefinition) {
  const label = lane.label || lane.location;
  const modes = lane.work_modes.map(workModeLabel).join(", ");
  return `${label} (${modes})`;
}

export function shortSearchName(name: string) {
  return name.split(/\s+[—–-]\s+/)[0]?.trim() || name;
}

export function discoveryLocationScope(lanes: DiscoverySearchLaneDefinition[]) {
  const labels = lanes.map(lane => {
    const label = (lane.label || lane.location).trim();
    const remoteOnly = lane.work_modes.length === 1 && lane.work_modes[0] === "remote";
    if (remoteOnly && /^(united states|usa|us)( remote)?$/i.test(label)) return "US remote";
    return label;
  });
  return [...new Set(labels)].join(" + ") || "No location scope";
}

export function hasDiscoveryRunSummary(summary: DiscoveryLastRunSummary) {
  return [summary.evaluated_count, summary.new_count, summary.updated_count, summary.enriched_count]
    .some(value => Number(value || 0) > 0);
}

export function storedDiscoveryRunKey() {
  try {
    return window.localStorage.getItem(DISMISSED_DISCOVERY_RUN_KEY) || "";
  } catch {
    return "";
  }
}

export function DiscoveryRunDetails({ summary }: { summary: DiscoveryLastRunSummary }) {
  const metrics = [
    ["Reviewed", summary.evaluated_count || 0],
    ["Eligible", summary.qualified_count || 0],
    ["Already in Hunter", summary.known_count || 0],
    ["Duplicates removed", summary.duplicate_count || 0],
    ["Screened out", summary.screened_count || 0],
    ["Held for later", summary.limited_count || 0],
    ["Source errors", summary.errors?.length || 0]
  ];
  return (
    <div className="discovery-completion-details">
      {metrics.map(([label, value]) => (
        <span key={String(label)}><strong>{value}</strong> {label}</span>
      ))}
    </div>
  );
}

export function workModeLabel(mode: DiscoverySearchLaneDefinition["work_modes"][number]) {
  return WORK_MODE_OPTIONS.find(option => option.id === mode)?.label || titleCase(mode);
}

export function discoveryCandidateMatches(candidate: DiscoveryCandidate, filter: DiscoveryFilter) {
  if (filter === "needs-decision") return candidate.status === "new";
  return candidate.status === filter;
}

export function legacyDiscoveryFilter(value: string | null) {
  if (!value) return "needs-decision";
  if (["new", "recommended", "pending", "source-verification", "needs-input", "all"].includes(value)) return "needs-decision";
  if (value === "ingested") return "pursued";
  return value;
}

export function uniqueValues(values: string[]) {
  return [...new Set(values.map(value => value.trim()).filter(Boolean))].sort((left, right) => left.localeCompare(right));
}

export function uniqueValuesInOrder(values: string[]) {
  return [...new Set(values.map(value => value.trim()).filter(Boolean))];
}

export function candidateMatchesExclusionTerms(candidate: DiscoveryCandidate, terms: string[]) {
  const text = candidate.title.toLowerCase();
  return terms.some(term => term.trim() && text.includes(term.trim().toLowerCase()));
}

export function processingLabel(candidate: DiscoveryCandidate) {
  if (candidate.review_state === "ready") return "Ready";
  if (candidate.review_state === "needs-qualification") return "Needs location verification";
  if (candidate.review_state === "needs-detail") return "Needs detail";
  if (candidate.freshness_status === "needs-review") return "Freshness could not be verified";
  if (candidate.review_state === "needs-freshness") return "Needs freshness";
  return "Failed extraction";
}

export function candidateLocationLabel(candidate: DiscoveryCandidate) {
  const location = candidate.location || "Location unknown";
  return candidate.work_mode ? `${location} · ${candidate.work_mode}` : location;
}

export function discoverySourceLabel(candidate: DiscoveryCandidate) {
  return candidate.source_platform === "adzuna"
    ? "Jobs by Adzuna"
    : titleCase(candidate.source_platform || "manual");
}

export function discoveryReasonSummary(reasons: Record<string, number> | undefined) {
  const labels: Record<string, string> = {
    "invalid-posting-page": "invalid posting pages",
    "title-exclusion": "title exclusions",
    "lane-mismatch": "location or work-mode mismatches",
    "lane-mismatch-after-enrichment": "location mismatches after enrichment",
    "not-interested-company": "not-interested companies",
    "the ATS URL is a board, redirect, or error page": "invalid ATS pages",
    "the role match score is below 45": "low-match roles",
    "the posting is from an aggregator without a verified employer source": "unverified aggregator roles",
    "the posting is closed": "closed postings",
    "automatic-quality-screen": "other quality screens"
  };
  return Object.entries(reasons || {})
    .filter(([, count]) => count > 0)
    .sort((left, right) => right[1] - left[1])
    .slice(0, 4)
    .map(([reason, count]) => `${count} ${labels[reason] || reason}`)
    .join(", ");
}

export function freshnessLabel(candidate: DiscoveryCandidate) {
  if (candidate.freshness_status === "confirmed-open") {
    return candidate.freshness_checked_at
      ? `Confirmed open ${dateOnlyLabel(candidate.freshness_checked_at)}`
      : "Confirmed open";
  }
  if (candidate.freshness_status === "closed") return "Closed or no longer accepting applications";
  if (candidate.freshness_status === "needs-review") return "Freshness needs review";
  return "Freshness not checked";
}

export function freshnessShortLabel(candidate: DiscoveryCandidate) {
  if (candidate.freshness_status === "confirmed-open") return "Open";
  if (candidate.freshness_status === "closed") return "Closed";
  if (candidate.freshness_status === "needs-review") return "Review";
  return "Unchecked";
}

export function sourceLabel(url: string) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

export function fitClass(score: string) {
  const value = Number(score || 0);
  if (value >= 70) return "fit-strong";
  if (value >= 45) return "fit-recommended";
  return "fit-low";
}

export function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}

export function sameStringSet(left: Set<string>, right: Set<string>) {
  if (left.size !== right.size) return false;
  return [...left].every(value => right.has(value));
}
