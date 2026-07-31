import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";
import { DownloadIcon, ExternalIcon, FilterIcon, ListIcon, SearchIcon } from "../components/Icons";
import { SortableHeader } from "../components/Primitives";
import {
  archiveCompany,
  checkCompanyPostings,
  dismissSuggestion,
  ingestDiscoveryCandidate,
  ingestCompanyCandidate,
  linkCompanyContact,
  mergeCompanies,
  researchCompany,
  resolveCompanyMetadataSuggestion,
  restoreCompany,
  trackCompany,
  unlinkCompanyContact,
  updateDiscoveryCandidate,
  updateCompanyCandidate,
  upsertCompany,
  type CompanyMetadataSuggestion
} from "../core/api";
import { routes } from "../core/routes";
import { dateOnlyLabel, titleCase } from "../core/format";
import { compareText, nextSortState, type SortDirection, type SortState } from "../core/tableSort";
import type { AppState, Company, CompanyCareerSource, CompanyMergeSuggestion, CompanyPostingCandidate, DiscoveryCandidate } from "../core/types";
import {
  RECOMMENDED_CANDIDATE_LIMIT,
  candidateFitScore,
  candidateRank,
  isRecommendedCandidate
} from "./candidateUtils";

type CompaniesPageProps = {
  data: AppState;
  refresh: () => Promise<AppState>;
};

type CompanyDetailPageProps = CompaniesPageProps & {
  createNew?: boolean;
};

type CompanyCandidateRow =
  | { source: "discovery"; candidate: DiscoveryCandidate }
  | { source: "career"; candidate: CompanyPostingCandidate };

const INTEREST_STATUSES = ["interested", "neutral", "not-interested", "archived"];
const DEFAULT_INTEREST_STATUSES = ["interested", "neutral"];
const TRACKING_STATUSES = ["tracked", "discovered"];
const COMPANY_CANDIDATE_PREVIEW_LIMIT = 50;
type CompanySortKey = "company" | "interest" | "tracking" | "careers_url" | "last_check";

export function CompaniesPage({ data, refresh }: CompaniesPageProps) {
  const [search, setSearch] = useState("");
  const [interestStatuses, setInterestStatuses] = useState<string[]>(DEFAULT_INTEREST_STATUSES);
  const [trackingStatuses, setTrackingStatuses] = useState<string[]>(TRACKING_STATUSES);
  const [checkingCompanyId, setCheckingCompanyId] = useState("");
  const [operationStatus, setOperationStatus] = useState("");
  const [sort, setSort] = useState<SortState<CompanySortKey>>({ key: "company", direction: "asc" });

  const rows = useMemo(() => {
    const query = search.toLowerCase();
    return data.companies
      .filter(company => {
        if (!interestStatuses.includes(company.interest_status)) return false;
        if (!trackingStatuses.includes(company.tracking_status)) return false;
        if (!query) return true;
        return [
          company.id,
          company.name,
          company.aliases,
          company.interest_status,
          company.tracking_status,
          company.website,
          company.careers_url,
          company.industry,
          company.company_size,
          company.notes,
          company.last_check_status
        ].join(" ").toLowerCase().includes(query);
      })
      .sort((a, b) => compareCompanyRows(a, b, sort));
  }, [data.companies, interestStatuses, search, sort, trackingStatuses]);

  function changeSort(key: CompanySortKey, initialDirection: SortDirection) {
    setSort(current => nextSortState(current, key, initialDirection));
  }

  function clearFilters() {
    setSearch("");
    setInterestStatuses(DEFAULT_INTEREST_STATUSES);
    setTrackingStatuses(TRACKING_STATUSES);
  }

  async function checkCareersFromTable(company: Company) {
    if (company.tracking_status !== "tracked" || !company.careers_url || checkingCompanyId) return;
    setCheckingCompanyId(company.id);
    setOperationStatus(`Checking ${company.name}...`);
    try {
      const result = await checkCompanyPostings(company.id);
      await refresh();
      setOperationStatus(`${company.name}: ${result.company.last_check_status}`);
    } catch (error) {
      setOperationStatus(`Could not check ${company.name}. ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setCheckingCompanyId("");
    }
  }

  async function trackFromTable(company: Company) {
    if (checkingCompanyId) return;
    setCheckingCompanyId(company.id);
    setOperationStatus(`Tracking ${company.name}...`);
    try {
      await trackCompany(company.id);
      await refresh();
      setOperationStatus(`${company.name} is now tracked. Career-page checks remain available when a careers URL is saved.`);
    } catch (error) {
      setOperationStatus(`Could not track ${company.name}. ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setCheckingCompanyId("");
    }
  }

  return (
    <section className="view-section" id="companies-view" aria-label="Companies">
      <article className="panel">
        <div className="toolbar" aria-label="Company tools">
          <label className="search">
            <span className="sr-only">Search companies</span>
            <SearchIcon />
            <input value={search} onChange={event => setSearch(event.target.value)} type="search" placeholder="Search companies, careers URLs, notes..." />
          </label>
          <MultiFilter label="Interest" values={INTEREST_STATUSES} selected={interestStatuses} onChange={setInterestStatuses} />
          <MultiFilter label="Tracking" values={TRACKING_STATUSES} selected={trackingStatuses} onChange={setTrackingStatuses} />
          <button className="button" type="button" onClick={clearFilters}><FilterIcon size={16} /> Clear</button>
          <a className="button icon-button" href="/api/companies/export" aria-label="Export company data" title="Export company data"><DownloadIcon /></a>
          <Link className="button primary" to={routes.companyNew}><ListIcon /> New Company</Link>
        </div>
        {operationStatus ? <div className="table-operation-status">{operationStatus}</div> : null}
        <div className="table-scroll">
          <table className="simple-table">
            <thead>
              <tr>
                <SortableHeader activeKey={sort.key} direction={sort.direction} label="Company" onSort={changeSort} sortKey="company" />
                <SortableHeader activeKey={sort.key} direction={sort.direction} label="Interest" onSort={changeSort} sortKey="interest" />
                <SortableHeader activeKey={sort.key} direction={sort.direction} label="Tracking" onSort={changeSort} sortKey="tracking" />
                <SortableHeader activeKey={sort.key} direction={sort.direction} label="Careers URL" onSort={changeSort} sortKey="careers_url" />
                <SortableHeader activeKey={sort.key} direction={sort.direction} initialDirection="desc" label="Last check" onSort={changeSort} sortKey="last_check" />
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(company => (
                <tr key={company.id} data-company-id={company.id}>
                  <td className="role-cell"><Link className="row-select" to={routes.companyDetail(company.id)}><strong>{company.name}</strong><span>{companyMetadataSummary(company) || company.aliases || company.id}</span></Link></td>
                  <td>{titleCase(company.interest_status)}</td>
                  <td><TrackingBadge company={company} /></td>
                  <td>{company.careers_url ? <a href={company.careers_url} target="_blank" rel="noreferrer">Open</a> : "None"}</td>
                  <td><LastCheckCell company={company} /></td>
                  <td>
                    <button
                      className="button compact table-action-button"
                      type="button"
                      disabled={(company.tracking_status === "tracked" && !company.careers_url) || Boolean(checkingCompanyId)}
                      onClick={() => company.tracking_status === "tracked"
                        ? checkCareersFromTable(company)
                        : trackFromTable(company)}
                      aria-label={company.tracking_status === "tracked"
                        ? `Check careers page for ${company.name}`
                        : `Track ${company.name}`}
                    >
                      {company.tracking_status === "tracked" ? <SearchIcon size={16} /> : <ListIcon size={16} />}
                      {checkingCompanyId === company.id
                        ? company.tracking_status === "tracked" ? "Checking" : "Tracking"
                        : company.tracking_status === "tracked" ? "Check" : "Track"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="empty-state" style={{ display: rows.length ? "none" : "block" }}>No companies match the current filters.</div>
        </div>
      </article>
    </section>
  );
}

function compareCompanyRows(left: Company, right: Company, sort: SortState<CompanySortKey>) {
  let result = 0;
  if (sort.key === "company") result = compareText(left.name, right.name, sort.direction);
  if (sort.key === "interest") result = compareText(left.interest_status, right.interest_status, sort.direction);
  if (sort.key === "tracking") result = compareText(left.tracking_status, right.tracking_status, sort.direction);
  if (sort.key === "careers_url") result = compareText(left.careers_url, right.careers_url, sort.direction);
  if (sort.key === "last_check") result = compareText(left.last_checked_at, right.last_checked_at, sort.direction);
  return result || compareText(left.name, right.name, "asc") || compareText(left.id, right.id, "asc");
}

function LastCheckCell({ company }: { company: Company }) {
  const status = company.last_check_status || "";
  const chip = lastCheckChip(status);
  return (
    <div className="last-check-cell">
      <span className={`last-check-chip ${chip.tone}`}>{chip.label}</span>
      <span className="last-check-detail">{lastCheckDetail(company)}</span>
    </div>
  );
}

function MultiFilter({
  label,
  values,
  selected,
  onChange
}: {
  label: string;
  values: string[];
  selected: string[];
  onChange: (values: string[]) => void;
}) {
  const allSelected = values.length === selected.length;
  const summary = allSelected ? "All" : selected.length === 1 ? titleCase(selected[0]) : `${selected.length} selected`;

  function toggle(value: string) {
    onChange(selected.includes(value) ? selected.filter(item => item !== value) : [...selected, value]);
  }

  return (
    <details className="filter multi-filter">
      <summary>{label} <span>{summary}</span></summary>
      <div className="multi-filter-menu">
        <label className="multi-filter-option">
          <input checked={allSelected} onChange={event => onChange(event.target.checked ? values : [])} type="checkbox" />
          All
        </label>
        {values.map(value => (
          <label className="multi-filter-option" key={value}>
            <input checked={selected.includes(value)} onChange={() => toggle(value)} type="checkbox" />
            {titleCase(value)}
          </label>
        ))}
      </div>
    </details>
  );
}

export function CompanyDetailPage({ data, refresh, createNew = false }: CompanyDetailPageProps) {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const isNewCompany = createNew || id === "new";
  const company = isNewCompany ? null : data.companies.find(row => row.id === id) || null;
  const invalidCompany = !isNewCompany && !company;
  const [operationStatus, setOperationStatus] = useState("");
  const [activeCandidateActionId, setActiveCandidateActionId] = useState("");
  const [isCheckingCareers, setIsCheckingCareers] = useState(false);
  const [isResearching, setIsResearching] = useState(false);
  const [isTracking, setIsTracking] = useState(false);
  const [isUpdatingInterest, setIsUpdatingInterest] = useState(false);
  const [isMerging, setIsMerging] = useState(false);
  const [activeSuggestionId, setActiveSuggestionId] = useState("");

  const linkedContactIds = useMemo(
    () => new Set(data.company_contacts.filter(link => link.company_id === company?.id).map(link => link.contact_id)),
    [company?.id, data.company_contacts]
  );
  const linkedContacts = useMemo(
    () => data.contacts.filter(contact => linkedContactIds.has(contact.id)),
    [data.contacts, linkedContactIds]
  );
  const linkedPostings = useMemo(
    () => data.applications
      .filter(app => app.company_id === company?.id)
      .sort((left, right) => Number(right.is_active) - Number(left.is_active)
        || (left.sort_due || "").localeCompare(right.sort_due || "")
        || (left.role || "").localeCompare(right.role || "")),
    [company?.id, data.applications]
  );
  const careerSource = useMemo(
    () => data.company_career_sources.find(source => source.company_id === company?.id) || null,
    [company?.id, data.company_career_sources]
  );
  const careerSourceEvidence = useMemo(
    () => parseEvidence(careerSource),
    [careerSource]
  );
  const candidates = useMemo(
    () => data.company_posting_candidates
      .filter(candidate => candidate.company_id === company?.id)
      .sort((a, b) => candidateRank(a.status) - candidateRank(b.status) || candidateFitScore(b) - candidateFitScore(a) || (b.last_seen_at || "").localeCompare(a.last_seen_at || "")),
    [company?.id, data.company_posting_candidates]
  );
  const discoveryRoles = useMemo(
    () => data.discovery_candidates
      .filter(candidate => candidate.company_id === company?.id)
      .sort((a, b) => candidateRank(a.status) - candidateRank(b.status)
        || Number(b.fit_score || 0) - Number(a.fit_score || 0)
        || (b.last_seen_at || "").localeCompare(a.last_seen_at || "")),
    [company?.id, data.discovery_candidates]
  );
  const mergeSuggestions = useMemo(
    () => (data.company_merge_suggestions || []).filter(
      suggestion => (
        suggestion.keep_company_id === company?.id || suggestion.merge_company_id === company?.id
      ) && !(data.dismissed_suggestion_ids || []).includes(`company-merge:${suggestion.id}`)
    ),
    [company?.id, data.company_merge_suggestions, data.dismissed_suggestion_ids]
  );
  const [candidateSearch, setCandidateSearch] = useState("");
  const recommendedCount = useMemo(
    () => Math.min(candidates.filter(candidate => isRecommendedCandidate(candidate, company?.last_checked_at || "")).length, RECOMMENDED_CANDIDATE_LIMIT),
    [candidates, company?.last_checked_at]
  );
  const candidateRows = useMemo<CompanyCandidateRow[]>(
    () => [
      ...discoveryRoles.map(candidate => ({ source: "discovery" as const, candidate })),
      ...candidates.map(candidate => ({ source: "career" as const, candidate }))
    ].sort(compareCompanyCandidateRows),
    [candidates, discoveryRoles]
  );
  const matchingCandidateRows = useMemo(
    () => candidateRows.filter(row => candidateIncludes(row.candidate, candidateSearch)),
    [candidateRows, candidateSearch]
  );
  const visibleCandidateRows = matchingCandidateRows.slice(0, COMPANY_CANDIDATE_PREVIEW_LIMIT);
  const availableContacts = useMemo(
    () => data.contacts.filter(contact => !linkedContactIds.has(contact.id)),
    [data.contacts, linkedContactIds]
  );
  const [contactId, setContactId] = useState(availableContacts[0]?.id || "");
  const metadataSuggestions = useMemo(
    () => parseCompanyMetadataSuggestions(company?.company_metadata_suggestions_json || ""),
    [company?.company_metadata_suggestions_json]
  );
  const decisionSuggestionId = company ? `company-decision:${company.id}` : "";
  const trackingSuggestionId = company ? `company-tracking:${company.id}` : "";
  const showDecisionSuggestion = Boolean(
    company?.decision_recommendation
    && !(data.dismissed_suggestion_ids || []).includes(decisionSuggestionId)
  );
  const showTrackingSuggestion = Boolean(
    company?.tracking_recommendation
    && !(data.dismissed_suggestion_ids || []).includes(trackingSuggestionId)
  );

  useEffect(() => {
    setContactId(availableContacts[0]?.id || "");
  }, [availableContacts]);

  async function saveCompany(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setOperationStatus("Saving company...");
    try {
      const result = await upsertCompany(company?.id || "", {
        name: String(form.get("name") || ""),
        aliases: String(form.get("aliases") || ""),
        interest_status: String(form.get("interest_status") || ""),
        website: String(form.get("website") || ""),
        careers_url: String(form.get("careers_url") || ""),
        industry: String(form.get("industry") || ""),
        company_size: String(form.get("company_size") || ""),
        company_profile_url: String(form.get("company_profile_url") || ""),
        notes: String(form.get("notes") || "")
      });
      await refresh();
      navigate(routes.companyDetail(result.company.id), { replace: isNewCompany });
      setOperationStatus("Company saved.");
    } catch (error) {
      setOperationStatus(`Could not save company. Run make serve-app. ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  async function checkCareers() {
    if (!company || company.tracking_status !== "tracked" || isCheckingCareers) return;
    setIsCheckingCareers(true);
    setOperationStatus("Checking careers page...");
    try {
      const result = await checkCompanyPostings(company.id);
      await refresh();
      const detailChecked = result.verification_count ? `; ${result.verification_count} detail checked` : "";
      const detailSkipped = result.verification_skipped_count ? `; ${result.verification_skipped_count} detail skipped` : "";
      setOperationStatus(`Check complete. ${result.new.length} new candidate${result.new.length === 1 ? "" : "s"}; ${result.recommended.length} recommended; ${result.unavailable_count} unavailable${detailChecked}${detailSkipped}.`);
    } catch (error) {
      setOperationStatus(`Could not check careers page. ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setIsCheckingCareers(false);
    }
  }

  async function researchCurrentCompany() {
    if (!company || isResearching) return;
    setIsResearching(true);
    setOperationStatus("Hunter is researching company information in the signed-in browser...");
    try {
      const result = await researchCompany(company.id);
      await refresh();
      const filled = result.applied_fields.length;
      const suggested = result.suggestions.length;
      setOperationStatus(
        filled || suggested
          ? `Research complete. Filled ${filled} blank field${filled === 1 ? "" : "s"} and added ${suggested} suggestion${suggested === 1 ? "" : "s"} for review.`
          : "Research complete. Hunter found no new company information."
      );
    } catch (error) {
      setOperationStatus(`Could not research company. ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setIsResearching(false);
    }
  }

  async function trackCurrentCompany() {
    if (!company || isTracking) return;
    setIsTracking(true);
    setOperationStatus("Adding company to explicit tracking...");
    try {
      await trackCompany(company.id);
      await refresh();
      setOperationStatus("Company is now tracked. Hunter can use its careers URL in Companies mode.");
    } catch (error) {
      setOperationStatus(`Could not track company. ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setIsTracking(false);
    }
  }

  async function updateInterestPreference(interestStatus: "neutral" | "not-interested") {
    if (!company || isUpdatingInterest) return;
    setIsUpdatingInterest(true);
    setOperationStatus(
      interestStatus === "not-interested"
        ? "Removing this company from Discovery..."
        : "Returning this company to Discovery..."
    );
    try {
      await upsertCompany(company.id, { interest_status: interestStatus });
      await refresh();
      setOperationStatus(
        interestStatus === "not-interested"
          ? "Company marked not interested. Its existing and future roles are hidden from Discovery."
          : "Company returned to neutral. Its stored roles can appear in Discovery again."
      );
    } catch (error) {
      setOperationStatus(`Could not update company interest. ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setIsUpdatingInterest(false);
    }
  }

  async function resolveMetadataSuggestion(suggestion: CompanyMetadataSuggestion, action: "apply" | "dismiss") {
    if (!company || activeSuggestionId) return;
    setActiveSuggestionId(suggestion.id);
    setOperationStatus(`${action === "apply" ? "Applying" : "Dismissing"} company information suggestion...`);
    try {
      await resolveCompanyMetadataSuggestion(company.id, suggestion.id, action);
      await refresh();
      setOperationStatus(action === "apply" ? "Company information updated." : "Suggestion dismissed.");
    } catch (error) {
      setOperationStatus(`Could not resolve suggestion. ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setActiveSuggestionId("");
    }
  }

  async function dismissCompanySuggestion(suggestionId: string) {
    if (!suggestionId || activeSuggestionId) return;
    setActiveSuggestionId(suggestionId);
    setOperationStatus("Dismissing Hunter suggestion...");
    try {
      await dismissSuggestion(suggestionId);
      await refresh();
      setOperationStatus("Suggestion dismissed. Company data was not changed.");
    } catch (error) {
      setOperationStatus(`Could not dismiss suggestion. ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setActiveSuggestionId("");
    }
  }

  async function archiveCurrentCompany() {
    if (!company) return;
    setOperationStatus("Archiving company...");
    try {
      await archiveCompany(company.id);
      await refresh();
      setOperationStatus("Company archived.");
    } catch (error) {
      setOperationStatus(`Could not archive company. ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  async function restoreCurrentCompany() {
    if (!company) return;
    setOperationStatus("Restoring company...");
    try {
      await restoreCompany(company.id, "neutral");
      await refresh();
      setOperationStatus("Company restored.");
    } catch (error) {
      setOperationStatus(`Could not restore company. ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  async function addContact() {
    if (!company || !contactId) return;
    setOperationStatus("Linking contact...");
    try {
      await linkCompanyContact(company.id, contactId);
      await refresh();
      setOperationStatus("Contact linked.");
    } catch (error) {
      setOperationStatus(`Could not link contact. ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  async function removeContact(contactIdToRemove: string) {
    if (!company) return;
    setOperationStatus("Removing contact...");
    try {
      await unlinkCompanyContact(company.id, contactIdToRemove);
      await refresh();
      setOperationStatus("Contact removed.");
    } catch (error) {
      setOperationStatus(`Could not remove contact. ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  async function ignoreCandidate(candidateId: string) {
    if (activeCandidateActionId) return;
    setActiveCandidateActionId(candidateId);
    setOperationStatus("Ignoring candidate...");
    try {
      await updateCompanyCandidate(candidateId, "ignored");
      await refresh();
      setOperationStatus("Candidate ignored.");
    } catch (error) {
      setOperationStatus(`Could not ignore candidate. ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setActiveCandidateActionId("");
    }
  }

  async function ingestCandidate(candidateId: string) {
    if (activeCandidateActionId) return;
    setActiveCandidateActionId(candidateId);
    setOperationStatus("Ingesting candidate...");
    try {
      await ingestCompanyCandidate(candidateId);
      await refresh();
      setOperationStatus("Candidate ingested.");
    } catch (error) {
      setOperationStatus(`Could not ingest candidate. ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setActiveCandidateActionId("");
    }
  }

  async function updateDiscoveryRole(candidateId: string, action: "ignored" | "ingested") {
    if (activeCandidateActionId) return;
    setActiveCandidateActionId(candidateId);
    setOperationStatus(`${action === "ignored" ? "Ignoring" : "Ingesting"} Discovery role...`);
    try {
      if (action === "ignored") await updateDiscoveryCandidate(candidateId, "ignored");
      else await ingestDiscoveryCandidate(candidateId);
      await refresh();
      setOperationStatus(action === "ignored" ? "Discovery role ignored." : "Discovery role ingested.");
    } catch (error) {
      setOperationStatus(`Could not update Discovery role. ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setActiveCandidateActionId("");
    }
  }

  async function mergeSuggestedCompany(suggestion: CompanyMergeSuggestion) {
    if (isMerging) return;
    setIsMerging(true);
    setOperationStatus("Merging company records and relinking roles...");
    try {
      const result = await mergeCompanies(suggestion.keep_company_id, suggestion.merge_company_id);
      await refresh();
      navigate(routes.companyDetail(result.company.id), { replace: true });
      setOperationStatus("Company records merged. Roles and relationships now use the canonical company.");
    } catch (error) {
      setOperationStatus(`Could not merge companies. ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setIsMerging(false);
    }
  }

  if (invalidCompany) return <Navigate to={routes.companies} replace />;

  if (isNewCompany) {
    return (
      <section className="view-section company-detail-page" aria-label="New Company">
        <div className="company-breadcrumb"><Link to={routes.companies}>Companies</Link><span>/</span><span>New company</span></div>
        <article className="panel company-create-panel">
          <div className="detail-topline">
            <div>
              <h2 id="company-form-title">New company</h2>
              <p>Add a company to track its careers source, roles, and relationships.</p>
            </div>
          </div>
          <CompanyForm company={null} onSubmit={saveCompany} />
          <div className="detail-status" role="status" aria-live="polite">{operationStatus}</div>
        </article>
      </section>
    );
  }

  if (!company) return <Navigate to={routes.companies} replace />;

  return (
    <section className="view-section company-detail-page" aria-label={company.name}>
      <div className="company-breadcrumb"><Link to={routes.companies}>Companies</Link><span>/</span><span>{company.name || "Unnamed company"}</span></div>
      <header className="company-hero panel">
        <div className="company-identity">
          <div className="company-monogram" aria-hidden="true">{company.name.trim().charAt(0).toUpperCase() || "?"}</div>
          <div>
            <div className="company-title-line">
              <h1>{company.name || "Unnamed company"}</h1>
              <TrackingBadge company={company} />
              <span className={`company-interest ${company.interest_status || "neutral"}`}>{titleCase(company.interest_status || "neutral")}</span>
            </div>
            <p>{companyMetadataSummary(company) || `Company record ${company.id}`}</p>
            {company.aliases ? <span className="company-aliases">Also known as {company.aliases}</span> : null}
          </div>
        </div>
        <div className="company-primary-actions">
          {company.interest_status === "not-interested" ? (
            <button className="button" type="button" disabled={isUpdatingInterest} onClick={() => void updateInterestPreference("neutral")}>
              {isUpdatingInterest ? "Updating…" : "Reconsider company"}
            </button>
          ) : company.interest_status !== "archived" ? (
            <button
              className="button"
              type="button"
              disabled={isUpdatingInterest}
              title="Hide this company’s existing and future roles from Discovery"
              onClick={() => void updateInterestPreference("not-interested")}
            >
              {isUpdatingInterest ? "Updating…" : "Not interested"}
            </button>
          ) : null}
          <button className="button" type="button" disabled={isResearching} onClick={researchCurrentCompany}>
            <SearchIcon size={16} /> {isResearching ? "Researching…" : "Research company"}
          </button>
          {company.tracking_status === "discovered" ? (
            <button className="button primary" type="button" disabled={isTracking} onClick={trackCurrentCompany}>
              <ListIcon size={16} /> {isTracking ? "Tracking…" : "Track company"}
            </button>
          ) : null}
          {company.website ? <a className="button" href={company.website} target="_blank" rel="noreferrer"><ExternalIcon size={16} /> Website</a> : null}
          {company.careers_url ? <a className="button" href={company.careers_url} target="_blank" rel="noreferrer"><ExternalIcon size={16} /> Careers</a> : null}
          <button className="button primary" type="button" disabled={company.tracking_status !== "tracked" || !company.careers_url || isCheckingCareers} onClick={checkCareers}>
            <SearchIcon size={16} /> {isCheckingCareers ? "Checking…" : "Check careers"}
          </button>
        </div>
      </header>

      <div className="company-overview-grid" aria-label="Company overview">
        <div className="company-stat" aria-label={`${candidates.length + discoveryRoles.length} candidates linked to ${company.name}`}>
          <span>Candidates</span><strong>{candidates.length + discoveryRoles.length}</strong><small>{discoveryRoles.length} Discovery · {candidates.length} career scan</small>
        </div>
        <Link className="company-stat" to={routes.postingsFiltered({ companies: company.name })} aria-label={`View tracked postings for ${company.name}`}>
          <span>Tracked postings</span><strong>{linkedPostings.length}</strong><small>{linkedPostings.filter(app => app.is_active).length} active</small>
        </Link>
        <Link className="company-stat" to={routes.contactsFiltered({ company_id: company.id })} aria-label={`View contacts linked to ${company.name}`}>
          <span>Contacts</span><strong>{linkedContacts.length}</strong><small>{linkedContacts.length ? "Linked to this company" : "No relationships yet"}</small>
        </Link>
        <Link className="company-stat" to={routes.candidatesFiltered({ mode: "discovery" })} aria-label="Review Discovery inbox">
          <span>Recommended</span><strong>{recommendedCount + (company.recommended_discovery_role_count || 0)}</strong><small>Across both role sources</small>
        </Link>
      </div>

      {operationStatus ? <div className="company-operation-status" role="status" aria-live="polite">{operationStatus}</div> : null}

      <div className="company-workspace">
        <div className="company-workspace-main">
          <article className="panel company-postings-panel">
            <div className="company-section-header">
              <div><h2>Postings</h2><p>Tracked opportunities connected to this company.</p></div>
              <Link className="text-link" to={routes.postingsFiltered({ companies: company.name })}>View all</Link>
            </div>
            <div className="company-posting-list">
              {linkedPostings.length ? linkedPostings.map(app => (
                <Link className="company-posting-row" key={app.id} to={routes.postingDetail(app.id)}>
                  <div className="company-posting-copy">
                    <strong>{app.role || app.id}</strong>
                    <span>{titleCase(app.stage)}{app.outcome ? ` · ${titleCase(app.outcome)}` : ""}</span>
                  </div>
                  <div className="company-posting-next">
                    <span>Next action</span>
                    <strong>{app.next_action || "None"}</strong>
                  </div>
                  <span className="company-posting-open" aria-hidden="true">→</span>
                </Link>
              )) : <div className="company-section-empty">No tracked postings yet.</div>}
            </div>
          </article>

          <article className="panel company-candidates-panel">
            <div className="company-section-header">
              <div>
                <h2>Candidates</h2>
                <p>Compare potential roles in one list.</p>
              </div>
              <Link className="text-link" to={routes.candidatesFiltered({ companies: company.id })}>Open workspace</Link>
            </div>
            <div className="company-candidate-toolbar">
              <label className="search">
                <span className="sr-only">Search candidates for {company.name}</span>
                <SearchIcon />
                <input value={candidateSearch} onChange={event => setCandidateSearch(event.target.value)} type="search" placeholder="Search title, location, status, or fit…" />
              </label>
              <div className="company-candidate-summary" aria-live="polite">
                <strong>{visibleCandidateRows.length}</strong>
                <span>
                  {candidateSearch && matchingCandidateRows.length > COMPANY_CANDIDATE_PREVIEW_LIMIT
                    ? `of ${matchingCandidateRows.length} matches`
                    : `shown from ${candidateRows.length}`}
                </span>
              </div>
            </div>
            <div className="company-candidate-list">
              {visibleCandidateRows.length ? visibleCandidateRows.map(row => {
                const { candidate } = row;
                const isDiscovery = row.source === "discovery";
                const sourceUrl = row.source === "discovery" ? row.candidate.canonical_url || row.candidate.url : row.candidate.url;
                const isActiveAction = activeCandidateActionId === candidate.id;
                const canIngest = row.source === "discovery"
                  ? row.candidate.status !== "ingested" && row.candidate.processing_status === "ready"
                  : row.candidate.status !== "ingested";
                const canIgnore = row.source === "discovery"
                  ? row.candidate.status === "new"
                  : row.candidate.status !== "ignored" && row.candidate.status !== "ingested";
                return (
                  <article className="company-candidate" key={`${row.source}-${candidate.id}`}>
                    <div className="company-candidate-copy">
                      <a className="company-candidate-title" href={sourceUrl} target="_blank" rel="noreferrer">{candidate.title || sourceUrl}</a>
                      <span className="company-candidate-location">{candidateLocationLabel(candidate)}</span>
                      <span className={candidate.fit_summary ? "candidate-fit-summary" : "candidate-fit-summary empty"}>
                        {candidate.fit_summary || "No fit summary yet."}
                      </span>
                    </div>
                    <dl className="company-candidate-facts">
                      <div><dt>Source</dt><dd>{candidateSourceLabel(row)}</dd></div>
                      <div><dt>Status</dt><dd>{titleCase(candidate.status)}</dd></div>
                      <div><dt>Fit</dt><dd>{candidate.fit_score ? `${candidate.fit_score} · ${fitLabel(candidate.fit_score)}` : "Not scored"}</dd></div>
                      <div><dt>Seen</dt><dd>{candidateDateLabel(candidate)}</dd></div>
                    </dl>
                    <div className="company-candidate-actions">
                      <a className="button compact" href={sourceUrl} target="_blank" rel="noreferrer">Open source</a>
                      <button
                        className="button compact primary"
                        type="button"
                        disabled={!canIngest || Boolean(activeCandidateActionId)}
                        onClick={() => isDiscovery ? updateDiscoveryRole(candidate.id, "ingested") : ingestCandidate(candidate.id)}
                      >
                        {isActiveAction ? "Ingesting..." : candidate.status === "ingested" ? "Ingested" : "Ingest"}
                      </button>
                      <button
                        className="button compact"
                        type="button"
                        disabled={!canIgnore || Boolean(activeCandidateActionId)}
                        onClick={() => isDiscovery ? updateDiscoveryRole(candidate.id, "ignored") : ignoreCandidate(candidate.id)}
                      >
                        {isActiveAction ? "Updating..." : candidate.status === "ignored" ? "Ignored" : "Ignore"}
                      </button>
                    </div>
                  </article>
                );
              }) : <div className="company-section-empty">
                {candidateSearch
                  ? "No candidates match this search."
                  : "No candidates have been recorded for this company yet."}
              </div>}
            </div>
          </article>

          <article className="panel company-rail-panel">
              <div className="company-section-header compact"><div><h2>Contacts</h2><p>{linkedContacts.length} linked relationship{linkedContacts.length === 1 ? "" : "s"}.</p></div></div>
              <div className="company-link-control">
                <select aria-label="Contact to link" value={contactId} onChange={event => setContactId(event.target.value)} disabled={!availableContacts.length}>
                  {availableContacts.length ? availableContacts.map(contact => <option key={contact.id} value={contact.id}>{contact.name || contact.id} · {contact.role || "No role"}</option>) : <option>No available contacts</option>}
                </select>
                <button className="button compact" type="button" disabled={!contactId} onClick={addContact}>Link</button>
              </div>
              <div className="company-relationship-list">
                {linkedContacts.length ? linkedContacts.map(contact => (
                  <div className="company-relationship" key={contact.id}>
                    <div><strong>{contact.name || contact.id}</strong><span>{[contact.role, contact.status].filter(Boolean).join(" · ") || "No details"}</span></div>
                    <button type="button" onClick={() => removeContact(contact.id)}>Unlink</button>
                  </div>
                )) : <div className="company-section-empty compact">No contacts linked yet.</div>}
              </div>
          </article>
        </div>

        <aside className="company-workspace-rail">
          {showTrackingSuggestion || showDecisionSuggestion || metadataSuggestions.length || mergeSuggestions.length ? (
            <article className="panel company-rail-panel company-suggestions-panel">
              <div className="company-section-header compact">
                <div><span className="eyebrow">Learn from your decisions</span><h2>Hunter suggestions</h2><p>Nothing changes automatically. Review each suggestion before applying it.</p></div>
              </div>
              {showDecisionSuggestion ? (
                <div className="company-tracking-suggestion">
                  <p>{company.decision_recommendation}</p>
                  <div className="company-suggestion-actions">
                    <button
                      className="button compact"
                      type="button"
                      disabled={isUpdatingInterest || Boolean(activeSuggestionId)}
                      onClick={() => void updateInterestPreference("not-interested")}
                    >
                      {isUpdatingInterest ? "Updating…" : "Mark not interested"}
                    </button>
                    <button className="button compact" type="button" disabled={Boolean(activeSuggestionId)} onClick={() => void dismissCompanySuggestion(decisionSuggestionId)}>Dismiss</button>
                  </div>
                </div>
              ) : null}
              {showTrackingSuggestion ? (
                <div className="company-tracking-suggestion">
                  <p>{company.tracking_recommendation}</p>
                  <div className="company-suggestion-actions">
                    {company.tracking_status === "discovered" ? (
                      <button className="button compact primary" type="button" disabled={isTracking || Boolean(activeSuggestionId)} onClick={trackCurrentCompany}>
                        Track company
                      </button>
                    ) : null}
                    <button className="button compact" type="button" disabled={Boolean(activeSuggestionId)} onClick={() => void dismissCompanySuggestion(trackingSuggestionId)}>Dismiss</button>
                  </div>
                </div>
              ) : null}
              {mergeSuggestions.map(suggestion => {
                const otherName = suggestion.keep_company_id === company.id
                  ? suggestion.merge_company_name
                  : suggestion.keep_company_name;
                return (
                  <div className="company-merge-suggestion" key={suggestion.id}>
                    <strong>Possible duplicate company</strong>
                    <p>{otherName}</p>
                    <span>{suggestion.reason}</span>
                    <div className="company-suggestion-actions">
                      <button className="button compact primary" type="button" disabled={isMerging || Boolean(activeSuggestionId)} onClick={() => mergeSuggestedCompany(suggestion)}>
                        {isMerging ? "Merging…" : suggestion.keep_company_id === company.id ? `Merge ${otherName} here` : `Merge into ${otherName}`}
                      </button>
                      <button className="button compact" type="button" disabled={Boolean(activeSuggestionId)} onClick={() => void dismissCompanySuggestion(`company-merge:${suggestion.id}`)}>Dismiss</button>
                    </div>
                  </div>
                );
              })}
              <div className="company-metadata-suggestions">
                {metadataSuggestions.map(suggestion => (
                  <article className="company-metadata-suggestion" key={suggestion.id}>
                    <strong>{companyFieldLabel(suggestion.field)}</strong>
                    <div><span>Current</span><p>{suggestion.current || "Blank"}</p></div>
                    <div><span>Hunter found</span><p>{suggestion.suggested}</p></div>
                    {suggestion.source_url ? <a href={suggestion.source_url} target="_blank" rel="noreferrer">View evidence</a> : null}
                    <div className="company-suggestion-actions">
                      <button className="button compact primary" type="button" disabled={Boolean(activeSuggestionId)} onClick={() => resolveMetadataSuggestion(suggestion, "apply")}>Apply</button>
                      <button className="button compact" type="button" disabled={Boolean(activeSuggestionId)} onClick={() => resolveMetadataSuggestion(suggestion, "dismiss")}>Dismiss</button>
                    </div>
                  </article>
                ))}
              </div>
            </article>
          ) : null}

          <article className="panel company-rail-panel">
            <div className="company-section-header compact">
              <div><h2>Company details</h2><p>Edit research context and tracking settings.</p></div>
            </div>
            <CompanyForm company={company} onSubmit={saveCompany} />
            <div className="company-record-actions">
              <a className="button compact icon-button" href={`/api/companies/export?id=${encodeURIComponent(company.id)}`} aria-label={`Export ${company.name || company.id} data`} title="Export company data"><DownloadIcon size={16} /></a>
              {company.interest_status === "archived"
                ? <button className="button compact" type="button" onClick={restoreCurrentCompany}>Restore company</button>
                : <button className="button compact" type="button" onClick={archiveCurrentCompany}>Archive company</button>}
            </div>
          </article>

          <article className="panel company-rail-panel">
            <div className="company-section-header compact"><div><h2>Careers source</h2><p>Source health and discovery evidence.</p></div></div>
            {company.tracking_status === "discovered" ? (
              <div className="company-section-empty">This company is stored from Discovery but is not in career-page tracking. Track it when you want Companies mode to scan its careers source.</div>
            ) : careerSource ? (
              <div className="company-source-body">
                <dl className="company-detail-list">
                  <div><dt>Platform</dt><dd>{titleCase(careerSource.platform_type.replaceAll("_", " "))}</dd></div>
                  <div><dt>Status</dt><dd>{titleCase(careerSource.status || "discovered")}</dd></div>
                  <div><dt>Last verified</dt><dd>{careerSource.last_verified_at ? dateOnlyLabel(careerSource.last_verified_at) : "Not verified"}</dd></div>
                </dl>
                {careerSourceEvidence.length ? <ul className="source-evidence">{careerSourceEvidence.map(item => <li key={item}>{item}</li>)}</ul> : null}
              </div>
            ) : <div className="company-section-empty">No source discovered yet. Run a careers check to inspect and save one.</div>}
          </article>

        </aside>
      </div>
    </section>
  );
}

function CompanyForm({ company, onSubmit }: { company: Company | null; onSubmit: (event: FormEvent<HTMLFormElement>) => void }) {
  return (
    <form className="management-form company-form" onSubmit={onSubmit} key={company?.id || "new-company"}>
      <label className="form-field full">Name <input name="name" type="text" required defaultValue={company?.name || ""} autoFocus={!company} /></label>
      <label className="form-field">Interest <select name="interest_status" defaultValue={company?.interest_status || "neutral"}>
        <option value="interested">Interested</option>
        <option value="neutral">Neutral</option>
        <option value="not-interested">Not interested</option>
        <option value="archived">Archived</option>
      </select></label>
      <label className="form-field">Aliases <input name="aliases" type="text" defaultValue={company?.aliases || ""} /></label>
      <label className="form-field">Website <input name="website" type="url" defaultValue={company?.website || ""} /></label>
      <label className="form-field">Careers URL <input name="careers_url" type="url" defaultValue={company?.careers_url || ""} /></label>
      <label className="form-field">Industry <input name="industry" type="text" defaultValue={company?.industry || ""} placeholder="For example, Software Development" /></label>
      <label className="form-field">Company size <input name="company_size" type="text" defaultValue={company?.company_size || ""} placeholder="For example, 201–500 employees" /></label>
      <label className="form-field full">Company profile URL <input name="company_profile_url" type="url" defaultValue={company?.company_profile_url || ""} /></label>
      {company?.company_metadata_checked_at ? (
        <div className="company-metadata-source form-field full">
          <span>Company information updated {dateOnlyLabel(company.company_metadata_checked_at)}.</span>
          {company.company_metadata_source && company.company_metadata_source !== "manual"
            ? <a href={company.company_metadata_source} target="_blank" rel="noreferrer">View source</a>
            : <span>Manually maintained</span>}
        </div>
      ) : null}
      <label className="form-field full">Notes <textarea name="notes" defaultValue={company?.notes || ""} /></label>
      <div className="form-field full"><button className="button primary" type="submit"><FilterIcon size={16} /> Save company</button></div>
    </form>
  );
}

function candidateDateLabel(candidate: CompanyPostingCandidate | DiscoveryCandidate) {
  const value = candidate.last_seen_at || ("first_seen_at" in candidate ? candidate.first_seen_at : candidate.captured_at);
  return value ? dateOnlyLabel(value) : "unknown";
}

function companyMetadataSummary(company: Company) {
  return [company.industry, company.company_size].filter(Boolean).join(" · ");
}

function TrackingBadge({ company }: { company: Company }) {
  return (
    <span className={`company-tracking ${company.tracking_status || "tracked"}`}>
      {company.tracking_status === "discovered" ? "Discovered" : "Tracked"}
    </span>
  );
}

function parseCompanyMetadataSuggestions(value: string): CompanyMetadataSuggestion[] {
  try {
    const parsed = JSON.parse(value || "[]") as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((item): item is CompanyMetadataSuggestion => (
      Boolean(item)
      && typeof item === "object"
      && typeof (item as CompanyMetadataSuggestion).id === "string"
      && typeof (item as CompanyMetadataSuggestion).field === "string"
      && typeof (item as CompanyMetadataSuggestion).suggested === "string"
    ));
  } catch {
    return [];
  }
}

function companyFieldLabel(field: CompanyMetadataSuggestion["field"]) {
  return {
    industry: "Industry",
    company_size: "Company size",
    company_profile_url: "Company profile",
    website: "Website"
  }[field];
}

function candidateLocationLabel(candidate: CompanyPostingCandidate | DiscoveryCandidate) {
  const location = conciseCandidateLocation(candidate.location);
  return candidate.work_mode ? `${location} · ${candidate.work_mode}` : location;
}

function conciseCandidateLocation(value: string) {
  const location = value.trim();
  if (!location) return "Location not listed";
  if (!location.includes("{")) return location;
  const plainPrefix = location.split(/,\s*\{/)[0].trim();
  if (!plainPrefix) return "Location details available at source";
  const parts = plainPrefix.split(",").map(part => part.trim()).filter(Boolean);
  return parts.slice(-3).join(", ") || "Location details available at source";
}

function candidateIncludes(candidate: CompanyPostingCandidate | DiscoveryCandidate, search: string) {
  const query = search.trim().toLowerCase();
  if (!query) return true;
  return [
    candidate.title,
    candidate.location,
    candidate.work_mode,
    candidate.status,
    candidate.fit_score,
    candidate.fit_summary,
    candidate.source_platform,
    "category" in candidate ? candidate.category : "",
    "scan_state" in candidate ? candidate.scan_state : "",
    "source_trust_label" in candidate ? candidate.source_trust_label : ""
  ].join(" ").toLowerCase().includes(query);
}

function compareCompanyCandidateRows(left: CompanyCandidateRow, right: CompanyCandidateRow) {
  return candidateRank(left.candidate.status) - candidateRank(right.candidate.status)
    || Number(right.candidate.fit_score || 0) - Number(left.candidate.fit_score || 0)
    || (right.candidate.last_seen_at || "").localeCompare(left.candidate.last_seen_at || "")
    || (left.candidate.title || "").localeCompare(right.candidate.title || "");
}

function candidateSourceLabel(row: CompanyCandidateRow) {
  const sourceType = row.source === "discovery" ? "Discovery" : "Career scan";
  const platform = row.candidate.source_platform
    ? titleCase(row.candidate.source_platform.replaceAll("_", " "))
    : "";
  return [sourceType, platform].filter(Boolean).join(" · ");
}

function fitLabel(score: string) {
  const band = fitBandForScore(score);
  if (band === "strong") return "Strong";
  if (band === "consider") return "Consider";
  return "Low";
}

function fitBandForScore(score: string) {
  const value = Number(score || 0);
  if (value >= 70) return "strong";
  if (value >= 45) return "consider";
  return "low";
}

function lastCheckChip(status: string) {
  const normalized = status.toLowerCase();
  if (!normalized) return { label: "Not checked", tone: "not-checked" };
  if (normalized.startsWith("ok:")) return { label: "OK", tone: "ok" };
  if (normalized.startsWith("partial:")) return { label: "Partial", tone: "checked" };
  if (normalized.startsWith("error:")) return { label: "Error", tone: "error" };
  return { label: "Checked", tone: "checked" };
}

function lastCheckDetail(company: Company) {
  return company.last_checked_at ? dateOnlyLabel(company.last_checked_at) : "Never";
}

function parseEvidence(source: CompanyCareerSource | null) {
  if (!source?.evidence) return [];
  try {
    const parsed = JSON.parse(source.evidence) as unknown;
    if (!Array.isArray(parsed)) return [source.evidence.trim()].filter(Boolean);
    return parsed.map(item => String(item).trim()).filter(Boolean);
  } catch {
    return [source.evidence.trim()].filter(Boolean);
  }
}
