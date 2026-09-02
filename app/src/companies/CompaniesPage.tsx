import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";
import { DownloadIcon, ExternalIcon, FilterIcon, GlobeIcon, ListIcon, SearchIcon, XIcon } from "../components/Icons";
import { SortableHeader } from "../components/Primitives";
import {
  archiveCompany,
  checkCompanyPostings,
  dismissSuggestion,
  pursueDiscoveryCandidate,
  pursueCompanyCandidate,
  linkCompanyContact,
  mergeCompanies,
  researchCompany,
  resolveCompanyMetadataSuggestion,
  restoreCompany,
  trackCompany,
  untrackCompany,
  unlinkCompanyContact,
  updateDiscoveryCandidate,
  updateCompanyCandidate,
  upsertCompany,
  type CompanyMetadataSuggestion
} from "../core/api";
import { routes } from "../core/routes";
import { dateOnlyLabel, titleCase } from "../core/format";
import { compareNumber, compareText, nextSortState, type SortDirection, type SortState } from "../core/tableSort";
import type { AppState, Company, CompanyCareerSource, CompanyDiscoveryJob, CompanyDiscoveryRunResult, CompanyMergeSuggestion, CompanyPostingCandidate, DiscoveryCandidate } from "../core/types";
import { selectionFromParam, selectionParamValue, sortFromParams, usePersistentViewParams } from "../core/viewState";
import {
  candidateFitScore,
  candidateRank,
} from "./candidateUtils";
import {
  companyListItemToLegacyCandidate,
  discoveryListItemToLegacyCandidate
} from "../core/readModelAdapters";
import {
  useCompanyCandidateList,
  useCompanyDetail,
  useDiscoveryCandidateList
} from "../core/readModelQueries";
import { readModelQueryKeys } from "../core/queryKeys";
import type { EntityDetail } from "../core/readModelTypes";

type CompaniesPageProps = {
  data: AppState;
  refresh: () => Promise<AppState>;
  discoveryJob?: CompanyDiscoveryJob | null;
  startDiscoveryJob?: (payload: CompanyDiscoveryJob["request"]) => Promise<CompanyDiscoveryJob>;
  startEvaluationJob?: (payload: CompanyDiscoveryJob["request"]) => Promise<CompanyDiscoveryJob>;
};

type CompanyDetailPageProps = CompaniesPageProps & {
  applyCompanyCandidateUpdates: (candidates: CompanyPostingCandidate[]) => void;
  createNew?: boolean;
};

type CompanyCandidateRow =
  | { source: "discovery"; candidate: DiscoveryCandidate }
  | { source: "career"; candidate: CompanyPostingCandidate };

const INTEREST_STATUSES = ["interested", "neutral", "not-interested", "archived"];
const DEFAULT_INTEREST_STATUSES = ["interested", "neutral"];
const DISCOVERY_REVIEW_STATES = ["ready", "needs-verification"];
const DISCOVERY_LOCATION_FIT_STATES = ["remote", "onsite", "both", "verify"];
const DISCOVERY_LOCATION_FIT_LABELS: Record<string, string> = {
  remote: "Remote eligible",
  onsite: "On-site eligible",
  both: "Remote + on-site",
  verify: "Needs verification"
};
const COMPANY_CANDIDATE_PREVIEW_LIMIT = 50;
const COMPANY_DISCOVERY_FOCUS = "interactive customer experiences, builder productivity and workflow platforms, complex technical products and services";
const COMPANY_SIZE_OPTIONS = [
  "11–50 employees",
  "51–200 employees",
  "201–500 employees",
  "501–1,000 employees",
  "1,001+ employees"
];
const DISCOVERY_SIZE_FILTERS = ["2–10 employees", ...COMPANY_SIZE_OPTIONS, "unknown"];
const DEFAULT_COMPANY_SIZES = ["51–200 employees", "201–500 employees"];
const COMPANY_DISCOVERY_SOURCES = [
  { id: "direct-employers", label: "Direct employer sites" },
  { id: "startup-directories", label: "Startup directories" },
  { id: "venture-portfolios", label: "Venture portfolios" },
  { id: "linkedin-companies", label: "Public company profiles" }
];
const DEFAULT_COMPANY_SOURCES = COMPANY_DISCOVERY_SOURCES.map(source => source.id);
const COMPANY_LOCATION_OPTIONS = [
  { id: "us-remote", label: "Remote eligibility" },
  { id: "metro-area", label: "On-site region" }
];
const DEFAULT_COMPANY_LOCATIONS = COMPANY_LOCATION_OPTIONS.map(option => option.id);
const COMPANY_REMOTE_REGION_KEY = "hunter-company-discovery-remote-region-v1";
const COMPANY_METRO_AREA_KEY = "hunter-company-discovery-metro-area-v1";
type CompanySortKey = "company" | "interest" | "careers_url" | "location" | "last_check";
type CompanyDiscoverySortKey = "company" | "fit" | "size" | "location";
type CompanyMode = "tracked" | "discovery";
const COMPANY_SORT_KEYS: CompanySortKey[] = ["company", "interest", "careers_url", "location", "last_check"];
const COMPANY_DISCOVERY_SORT_KEYS: CompanyDiscoverySortKey[] = ["company", "fit", "size", "location"];

export function CompaniesPage({ data, refresh, discoveryJob = null, startDiscoveryJob, startEvaluationJob }: CompaniesPageProps) {
  const { params: viewParams, updateParams: updateViewParams } = usePersistentViewParams("companies");
  const mode: CompanyMode = viewParams.get("mode") === "discovery" ? "discovery" : "tracked";
  const [discoveryModalOpen, setDiscoveryModalOpen] = useState(false);
  const search = viewParams.get("q") || "";
  const interestStatuses = selectionFromParam(viewParams.get("interest"), INTEREST_STATUSES, DEFAULT_INTEREST_STATUSES);
  const trackedLocationFitStates = selectionFromParam(viewParams.get("tracked_location"), DISCOVERY_LOCATION_FIT_STATES, DISCOVERY_LOCATION_FIT_STATES);
  const discoveryReviewStates = selectionFromParam(viewParams.get("review"), DISCOVERY_REVIEW_STATES, DISCOVERY_REVIEW_STATES);
  const discoverySizeFilters = selectionFromParam(viewParams.get("size"), DISCOVERY_SIZE_FILTERS, DISCOVERY_SIZE_FILTERS);
  const discoveryLocationFitStates = selectionFromParam(viewParams.get("location"), DISCOVERY_LOCATION_FIT_STATES, DISCOVERY_LOCATION_FIT_STATES);
  const [checkingCompanyId, setCheckingCompanyId] = useState("");
  const [pendingUntrackCompany, setPendingUntrackCompany] = useState<Company | null>(null);
  const [operationStatus, setOperationStatus] = useState("");
  const [discoveryFocus, setDiscoveryFocus] = useState(COMPANY_DISCOVERY_FOCUS);
  const [discoverySizes, setDiscoverySizes] = useState<string[]>(DEFAULT_COMPANY_SIZES);
  const [discoverySources, setDiscoverySources] = useState<string[]>(DEFAULT_COMPANY_SOURCES);
  const [discoveryLocations, setDiscoveryLocations] = useState<string[]>(DEFAULT_COMPANY_LOCATIONS);
  const [remoteRegion, setRemoteRegion] = useState(() => storedDiscoveryPreference(COMPANY_REMOTE_REGION_KEY, "United States"));
  const [metroArea, setMetroArea] = useState(() => storedDiscoveryPreference(COMPANY_METRO_AREA_KEY, "Minneapolis-Saint Paul metro"));
  const sort = sortFromParams(viewParams, "sort", "direction", COMPANY_SORT_KEYS, { key: "company", direction: "asc" });
  const discoverySort = sortFromParams(viewParams, "discovery_sort", "discovery_direction", COMPANY_DISCOVERY_SORT_KEYS, { key: "fit", direction: "desc" });
  const isDiscovering = discoveryJob?.status === "queued" || discoveryJob?.status === "running";
  const lastDiscoveryRun = discoveryJob?.job_type !== "company-evaluation"
    ? discoveryJob?.result as CompanyDiscoveryRunResult | null
    : null;

  useEffect(() => {
    storeDiscoveryPreference(COMPANY_REMOTE_REGION_KEY, remoteRegion);
  }, [remoteRegion]);

  useEffect(() => {
    storeDiscoveryPreference(COMPANY_METRO_AREA_KEY, metroArea);
  }, [metroArea]);

  const trackedRows = useMemo(() => {
    const query = search.toLowerCase();
    return data.companies
      .filter(company => {
        if (company.tracking_status !== "tracked") return false;
        if (!interestStatuses.includes(company.interest_status)) return false;
        if (!trackedLocationFitStates.includes(companyDiscoveryLocationFitState(company.company_location_fit))) return false;
        if (!query) return true;
        return [
          company.id,
          company.name,
          company.aliases,
          company.interest_status,
          company.website,
          company.careers_url,
          company.industry,
          company.company_size,
          company.company_location_fit,
          company.company_location,
          company.company_remote_policy,
          company.notes,
          company.last_check_status
        ].join(" ").toLowerCase().includes(query);
      })
      .sort((a, b) => compareCompanyRows(a, b, sort));
  }, [data.companies, interestStatuses, search, sort, trackedLocationFitStates]);

  const allDiscoveryRows = useMemo(
    () => data.companies.filter(company => (
      company.tracking_status === "discovered"
      && interestStatuses.includes(company.interest_status)
    )),
    [data.companies, interestStatuses]
  );

  const discoveryRows = useMemo(() => {
    const query = search.trim().toLowerCase();
    return allDiscoveryRows
      .filter(company => {
        if (!discoveryReviewStates.includes(companyDiscoveryReviewState(company))) return false;
        if (!discoverySizeFilters.includes(companyDiscoverySizeFilter(company.company_size))) return false;
        if (!discoveryLocationFitStates.includes(companyDiscoveryLocationFitState(company.company_location_fit))) return false;
        if (!query) return true;
        return [
          company.id,
          company.name,
          company.aliases,
          company.industry,
          company.company_size,
          company.company_location_fit,
          company.company_location,
          company.company_remote_policy,
          company.company_location_evidence,
          company.company_fit_summary,
          company.company_discovery_source,
          company.company_discovery_query,
          company.company_discovery_evidence,
          companyDiscoverySourceLabel(company)
        ].join(" ").toLowerCase().includes(query);
      })
      .sort((left, right) => compareDiscoveryCompanyRows(left, right, discoverySort));
  }, [allDiscoveryRows, discoveryLocationFitStates, discoveryReviewStates, discoverySizeFilters, discoverySort, search]);

  function changeSort(key: CompanySortKey, initialDirection: SortDirection) {
    const next = nextSortState(sort, key, initialDirection);
    updateViewParams({
      sort: next.key === "company" ? null : next.key,
      direction: next.direction === "asc" ? null : next.direction
    });
  }

  function changeDiscoverySort(key: CompanyDiscoverySortKey, initialDirection: SortDirection) {
    const next = nextSortState(discoverySort, key, initialDirection);
    updateViewParams({
      discovery_sort: next.key === "fit" ? null : next.key,
      discovery_direction: next.direction === "desc" ? null : next.direction
    });
  }

  function clearFilters() {
    updateViewParams({
      q: null,
      interest: null,
      tracked_location: null,
      review: null,
      size: null,
      location: null,
      sort: null,
      direction: null,
      discovery_sort: null,
      discovery_direction: null
    });
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

  function requestUntrackFromTable(company: Company) {
    if (checkingCompanyId || company.tracking_status !== "tracked") return;
    setPendingUntrackCompany(company);
  }

  async function confirmUntrackFromTable() {
    const company = pendingUntrackCompany;
    if (!company || checkingCompanyId || company.tracking_status !== "tracked") return;
    setCheckingCompanyId(company.id);
    setOperationStatus(`Moving ${company.name} back to Discovery...`);
    try {
      await untrackCompany(company.id);
      await refresh();
      setOperationStatus(`${company.name} moved to Discovery. Existing company data was kept.`);
    } catch (error) {
      setOperationStatus(`Could not stop tracking ${company.name}. ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setCheckingCompanyId("");
      setPendingUntrackCompany(null);
    }
  }

  async function discoverCompanies(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isDiscovering || !discoveryFocus.trim() || !discoverySizes.length || !discoverySources.length || !discoveryLocations.length) return;
    setOperationStatus("Starting company discovery…");
    try {
      if (!startDiscoveryJob) throw new Error("Background company discovery is unavailable.");
      const job = await startDiscoveryJob({
        focus: discoveryFocus,
        sizes: discoverySizes,
        sources: discoverySources,
        locations: discoveryLocations,
        remote_region: remoteRegion,
        metro_area: metroArea
      });
      setOperationStatus(job.message);
      updateViewParams({ mode: "discovery" });
      setDiscoveryModalOpen(false);
    } catch (error) {
      setOperationStatus(`Could not discover companies. ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  async function evaluateDiscoveryCompanies() {
    if (isDiscovering) return;
    setOperationStatus("Starting evaluation for Discovery companies…");
    try {
      if (!startEvaluationJob) throw new Error("Background company evaluation is unavailable.");
      const job = await startEvaluationJob({
        focus: discoveryFocus,
        sizes: discoverySizes,
        locations: discoveryLocations,
        remote_region: remoteRegion,
        metro_area: metroArea,
        tracking_status: "discovered",
        force: true,
        reason: "discovery-backfill"
      });
      setOperationStatus(job.message);
      updateViewParams({ mode: "discovery" });
    } catch (error) {
      setOperationStatus(`Could not evaluate Discovery companies. ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  async function dismissFromDiscovery(company: Company) {
    if (checkingCompanyId) return;
    setCheckingCompanyId(company.id);
    setOperationStatus(`Marking ${company.name} not interested...`);
    try {
      await upsertCompany(company.id, { interest_status: "not-interested" });
      await refresh();
      setOperationStatus(`${company.name} is no longer in company discovery or future role discovery.`);
    } catch (error) {
      setOperationStatus(`Could not update ${company.name}. ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setCheckingCompanyId("");
    }
  }

  return (
    <section className="view-section" id="companies-view" aria-labelledby="companies-title">
      <h1 className="sr-only" id="companies-title">Companies</h1>
      <article className="panel">
        <div className="toolbar" aria-label="Company tools">
          <label className="search">
            <span className="sr-only">Search companies</span>
            <SearchIcon />
            <input value={search} onChange={event => updateViewParams({ q: event.target.value || null })} type="search" placeholder="Search companies, websites, fit notes..." />
          </label>
          <MultiFilter label="Interest" values={INTEREST_STATUSES} selected={interestStatuses} onChange={values => updateViewParams({ interest: selectionParamValue(values, INTEREST_STATUSES, DEFAULT_INTEREST_STATUSES) })} />
          {mode === "tracked" ? (
            <MultiFilter label="Location fit" values={DISCOVERY_LOCATION_FIT_STATES} valueLabels={DISCOVERY_LOCATION_FIT_LABELS} selected={trackedLocationFitStates} onChange={values => updateViewParams({ tracked_location: selectionParamValue(values, DISCOVERY_LOCATION_FIT_STATES, DISCOVERY_LOCATION_FIT_STATES) })} />
          ) : null}
          {mode === "discovery" ? (
            <>
              <MultiFilter label="Review" values={DISCOVERY_REVIEW_STATES} selected={discoveryReviewStates} onChange={values => updateViewParams({ review: selectionParamValue(values, DISCOVERY_REVIEW_STATES, DISCOVERY_REVIEW_STATES) })} />
              <MultiFilter label="Size" values={DISCOVERY_SIZE_FILTERS} selected={discoverySizeFilters} onChange={values => updateViewParams({ size: selectionParamValue(values, DISCOVERY_SIZE_FILTERS, DISCOVERY_SIZE_FILTERS) })} />
              <MultiFilter label="Location fit" values={DISCOVERY_LOCATION_FIT_STATES} valueLabels={DISCOVERY_LOCATION_FIT_LABELS} selected={discoveryLocationFitStates} onChange={values => updateViewParams({ location: selectionParamValue(values, DISCOVERY_LOCATION_FIT_STATES, DISCOVERY_LOCATION_FIT_STATES) })} />
            </>
          ) : null}
          <button className="button" type="button" onClick={clearFilters}><FilterIcon size={16} /> Clear</button>
          <a className="button icon-button" href="/api/companies/export" aria-label="Export company data" title="Export company data"><DownloadIcon /></a>
          <Link className="button primary" to={routes.companyNew}><ListIcon /> New Company</Link>
        </div>
        <div className="company-mode-row">
          <div className="candidate-mode-switch company-mode-switch" role="tablist" aria-label="Company mode">
            <button className={mode === "tracked" ? "active" : ""} type="button" role="tab" aria-selected={mode === "tracked"} onClick={() => updateViewParams({ mode: null })}>Tracked</button>
            <button className={mode === "discovery" ? "active" : ""} type="button" role="tab" aria-selected={mode === "discovery"} onClick={() => updateViewParams({ mode: "discovery" })}>Discovery</button>
          </div>
          <button className="button primary company-discovery-open" type="button" onClick={() => setDiscoveryModalOpen(true)}>
            <SearchIcon size={16} /> Find Companies
          </button>
          {mode === "discovery" ? (
            <button className="button company-discovery-open" type="button" disabled={isDiscovering} onClick={evaluateDiscoveryCompanies}>
              <SearchIcon size={16} /> Evaluate Discovery
            </button>
          ) : null}
        </div>
        {mode === "discovery" ? (
          <div className="company-discovery-scroll">
            <CompanyDiscoveryWorkspace
              activeCompanyId={checkingCompanyId}
              lastRun={lastDiscoveryRun}
              onDismiss={dismissFromDiscovery}
              onSort={changeDiscoverySort}
              onTrack={trackFromTable}
              operationStatus={discoveryJob?.message || operationStatus}
              rows={discoveryRows}
              sort={discoverySort}
              totalRows={allDiscoveryRows.length}
            />
          </div>
        ) : (
          <>
            {operationStatus ? <div className="table-operation-status">{operationStatus}</div> : null}
            <CompaniesTable
              checkingCompanyId={checkingCompanyId}
              onCheck={checkCareersFromTable}
              onTrack={trackFromTable}
              onUntrack={requestUntrackFromTable}
              rows={trackedRows}
              sort={sort}
              onSort={changeSort}
            />
          </>
        )}
      </article>
      {discoveryModalOpen ? (
        <CompanyDiscoveryModal
          focus={discoveryFocus}
          isSearching={isDiscovering}
          metroArea={metroArea}
          onClose={() => setDiscoveryModalOpen(false)}
          onFocusChange={setDiscoveryFocus}
          onLocationsChange={setDiscoveryLocations}
          onMetroAreaChange={setMetroArea}
          onRemoteRegionChange={setRemoteRegion}
          onSearch={discoverCompanies}
          onSizesChange={setDiscoverySizes}
          onSourcesChange={setDiscoverySources}
          remoteRegion={remoteRegion}
          selectedLocations={discoveryLocations}
          selectedSizes={discoverySizes}
          selectedSources={discoverySources}
        />
      ) : null}
      {pendingUntrackCompany ? (
        <CompanyUntrackConfirmationModal
          company={pendingUntrackCompany}
          isConfirming={checkingCompanyId === pendingUntrackCompany.id}
          onClose={() => { if (!checkingCompanyId) setPendingUntrackCompany(null); }}
          onConfirm={confirmUntrackFromTable}
        />
      ) : null}
    </section>
  );
}

function CompanyDiscoveryWorkspace({
  activeCompanyId,
  lastRun,
  onDismiss,
  onSort,
  onTrack,
  operationStatus,
  rows,
  sort,
  totalRows
}: {
  activeCompanyId: string;
  lastRun: CompanyDiscoveryRunResult | null;
  onDismiss: (company: Company) => Promise<void>;
  onSort: (key: CompanyDiscoverySortKey, initialDirection: SortDirection) => void;
  onTrack: (company: Company) => Promise<void>;
  operationStatus: string;
  rows: Company[];
  sort: SortState<CompanyDiscoverySortKey>;
  totalRows: number;
}) {
  const status = operationStatus || (
    lastRun
      ? `${lastRun.review_count} compan${lastRun.review_count === 1 ? "y is" : "ies are"} ready for review. Nothing was tracked automatically.`
      : "Searches use the configured OpenAI API and keep each focus lane bounded. Nothing is tracked automatically."
  );

  return (
    <div className="company-discovery-workspace">
      <section className="company-discovery-results" aria-labelledby="company-discovery-heading">
        <div className="company-discovery-results-header">
          <h2 id="company-discovery-heading">Company discovery</h2>
          <p>{rows.length} shown from {totalRows} discovered companies. {status}</p>
        </div>
        <div className="table-scroll company-discovery-table-scroll">
          <table className="simple-table company-discovery-table">
            <thead>
              <tr>
                <SortableHeader activeKey={sort.key} direction={sort.direction} label="Company" onSort={onSort} sortKey="company" />
                <SortableHeader activeKey={sort.key} direction={sort.direction} initialDirection="desc" label="Fit" onSort={onSort} sortKey="fit" />
                <SortableHeader activeKey={sort.key} direction={sort.direction} label="Size" onSort={onSort} sortKey="size" />
                <SortableHeader activeKey={sort.key} direction={sort.direction} label="Location fit" onSort={onSort} sortKey="location" />
                <th>Why it fits</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(company => (
                <tr key={company.id}>
                  <td className="role-cell">
                    <Link className="row-select" to={routes.companyDetail(company.id)}>
                      <strong>{company.name}</strong>
                      <span>{company.industry || "Industry needs verification"}{company.website ? "" : " · Website needs verification"}</span>
                    </Link>
                  </td>
                  <td><CompanyFitScore company={company} /></td>
                  <td>{company.company_size || "Verify"}</td>
                  <td><CompanyLocationFit company={company} /></td>
                  <td className="company-fit-summary">{company.company_fit_summary || "Profile fit needs review."}</td>
                  <td>
                    <div className="company-discovery-actions">
                      {company.website ? (
                        <a className="button compact icon-button" href={company.website} target="_blank" rel="noreferrer" aria-label={`Open ${company.name} website`} title={`Open ${company.name} website`}>
                          <GlobeIcon size={15} />
                        </a>
                      ) : null}
                      <CompanyDiscoverySource company={company} />
                      <button className="button compact" type="button" disabled={Boolean(activeCompanyId)} onClick={() => onTrack(company)}>
                        <ListIcon size={14} /> {activeCompanyId === company.id ? "Tracking" : "Track"}
                      </button>
                      <button className="button compact secondary" type="button" disabled={Boolean(activeCompanyId)} onClick={() => onDismiss(company)}>
                        Not interested
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="empty-state" style={{ display: rows.length ? "none" : "block" }}>
            No discovered companies match the current filters.
          </div>
        </div>
      </section>
    </div>
  );
}

function CompanyDiscoverySource({ company }: { company: Company }) {
  const label = companyDiscoverySourceLabel(company);
  if (company.company_discovery_source_url) {
    return <a className="button compact icon-button" href={company.company_discovery_source_url} target="_blank" rel="noreferrer" aria-label={`Open ${company.name} discovery source: ${label}`} title={`Open discovery source: ${label}`}><ExternalIcon size={15} /></a>;
  }
  if (!company.company_discovery_source && company.discovery_role_count > 0) {
    return <Link className="button compact icon-button" to={routes.candidatesFiltered({ mode: "discovery" })} aria-label={`View ${company.name} role discovery source`} title="View role discovery source"><ExternalIcon size={15} /></Link>;
  }
  return null;
}

function CompanyDiscoveryModal({
  focus,
  isSearching,
  metroArea,
  onClose,
  onFocusChange,
  onLocationsChange,
  onMetroAreaChange,
  onRemoteRegionChange,
  onSearch,
  onSizesChange,
  onSourcesChange,
  remoteRegion,
  selectedLocations,
  selectedSizes,
  selectedSources
}: {
  focus: string;
  isSearching: boolean;
  metroArea: string;
  onClose: () => void;
  onFocusChange: (value: string) => void;
  onLocationsChange: (values: string[]) => void;
  onMetroAreaChange: (value: string) => void;
  onRemoteRegionChange: (value: string) => void;
  onSearch: (event: FormEvent<HTMLFormElement>) => Promise<void>;
  onSizesChange: (values: string[]) => void;
  onSourcesChange: (values: string[]) => void;
  remoteRegion: string;
  selectedLocations: string[];
  selectedSizes: string[];
  selectedSources: string[];
}) {
  useEffect(() => {
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);

  return (
    <div className="modal-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
      <article className="modal company-discovery-modal" role="dialog" aria-modal="true" aria-labelledby="company-discovery-modal-title">
        <div className="modal-header">
          <div className="company-discovery-intro">
            <h2 id="company-discovery-modal-title">Find Companies</h2>
            <p>Search startup directories, venture portfolios, and company profiles before roles reach the major boards.</p>
          </div>
          <button className="button compact" type="button" onClick={onClose}><XIcon size={18} /> Close</button>
        </div>
        <form className="company-discovery-composer" onSubmit={onSearch}>
          <label className="company-discovery-focus">
            <span className="sr-only">Company search focus</span>
            <SearchIcon />
            <input autoFocus value={focus} onChange={event => onFocusChange(event.target.value)} type="text" placeholder="Describe the products, customers, and work you want to find..." required />
          </label>
          <div className="company-discovery-controls">
            <fieldset>
              <legend>Company size</legend>
              <div className="company-discovery-options">
                {COMPANY_SIZE_OPTIONS.map(size => (
                  <label key={size}>
                    <input type="checkbox" checked={selectedSizes.includes(size)} onChange={() => onSizesChange(toggleSelected(selectedSizes, size))} />
                    {size.replace(" employees", "")}
                  </label>
                ))}
              </div>
            </fieldset>
            <fieldset>
              <legend>Location eligibility</legend>
              <div className="company-location-options">
                <div className="company-location-option">
                  <label><input type="checkbox" checked={selectedLocations.includes("us-remote")} onChange={() => onLocationsChange(toggleSelected(selectedLocations, "us-remote"))} />Remote region</label>
                  <input aria-label="Remote hiring region" value={remoteRegion} onChange={event => onRemoteRegionChange(event.target.value)} disabled={!selectedLocations.includes("us-remote")} placeholder="United States" required={selectedLocations.includes("us-remote")} />
                </div>
                <div className="company-location-option">
                  <label><input type="checkbox" checked={selectedLocations.includes("metro-area")} onChange={() => onLocationsChange(toggleSelected(selectedLocations, "metro-area"))} />On-site region</label>
                  <input aria-label="Eligible on-site region" value={metroArea} onChange={event => onMetroAreaChange(event.target.value)} disabled={!selectedLocations.includes("metro-area")} placeholder="On-site region" required={selectedLocations.includes("metro-area")} />
                </div>
              </div>
            </fieldset>
            <fieldset>
              <legend>Sources</legend>
              <div className="company-discovery-options">
                {COMPANY_DISCOVERY_SOURCES.map(source => (
                  <label key={source.id}><input type="checkbox" checked={selectedSources.includes(source.id)} onChange={() => onSourcesChange(toggleSelected(selectedSources, source.id))} />{source.label}</label>
                ))}
              </div>
            </fieldset>
          </div>
          <div className="company-discovery-modal-actions">
            <button className="button" type="button" onClick={onClose}>Cancel</button>
            <button className="button primary company-discovery-submit" type="submit" disabled={isSearching || !focus.trim() || !selectedSizes.length || !selectedSources.length || !selectedLocations.length || (selectedLocations.includes("us-remote") && !remoteRegion.trim()) || (selectedLocations.includes("metro-area") && !metroArea.trim())}>
              <SearchIcon size={16} /> {isSearching ? "Searching sources" : "Search sources"}
            </button>
          </div>
        </form>
      </article>
    </div>
  );
}

function CompanyUntrackConfirmationModal({
  company,
  isConfirming,
  onClose,
  onConfirm
}: {
  company: Company;
  isConfirming: boolean;
  onClose: () => void;
  onConfirm: () => Promise<void>;
}) {
  useEffect(() => {
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape" && !isConfirming) onClose();
    }
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [isConfirming, onClose]);

  return (
    <div className="modal-backdrop" onMouseDown={event => { if (event.target === event.currentTarget && !isConfirming) onClose(); }}>
      <article className="modal company-confirmation-modal" role="alertdialog" aria-modal="true" aria-labelledby="company-untrack-modal-title" aria-describedby="company-untrack-modal-description">
        <div className="company-confirmation-body">
          <span className="company-confirmation-eyebrow">Stop tracking</span>
          <h2 id="company-untrack-modal-title">Move {company.name} to Discovery?</h2>
          <p id="company-untrack-modal-description">Hunter will stop including this company in automatic career-page scans.</p>
          <div className="company-confirmation-impact">
            <strong>Your existing data stays connected</strong>
            <span>Candidates, postings, contacts, research, and saved links will be kept. You can track the company again at any time.</span>
          </div>
        </div>
        <div className="company-confirmation-actions">
          <button className="button" type="button" disabled={isConfirming} onClick={onClose} autoFocus>Keep tracked</button>
          <button className="button primary" type="button" disabled={isConfirming} onClick={() => void onConfirm()}>
            {isConfirming ? "Moving…" : "Move to Discovery"}
          </button>
        </div>
      </article>
    </div>
  );
}

function companyDiscoveryReviewState(company: Company) {
  if (company.company_evaluation_status === "ready") return "ready";
  if (
    !company.company_evaluation_status
    && company.website
    && company.company_size
    && company.company_location_fit
    && company.company_fit_score
  ) return "ready";
  return "needs-verification";
}

function companyEvaluationStatusLabel(company: Company) {
  const labels: Record<string, string> = {
    pending: "Evaluation queued",
    evaluating: "Evaluating company…",
    ready: "Evaluation complete",
    "needs-verification": "Evaluation complete · verification needed",
    failed: "Evaluation failed · retry available"
  };
  return labels[company.company_evaluation_status] || "Not evaluated";
}

function companyDiscoverySizeFilter(value: string) {
  const minimum = Number.parseInt((value.match(/\d[\d,]*/) || [""])[0].replaceAll(",", ""), 10);
  if (!Number.isFinite(minimum)) return "unknown";
  if (minimum <= 10) return "2–10 employees";
  if (minimum <= 50) return "11–50 employees";
  if (minimum <= 200) return "51–200 employees";
  if (minimum <= 500) return "201–500 employees";
  if (minimum <= 1_000) return "501–1,000 employees";
  return "1,001+ employees";
}

function companyDiscoveryLocationFitState(value: string) {
  if (value === "us-remote") return "remote";
  if (value === "metro-area" || value === "twin-cities") return "onsite";
  if (value === "both") return "both";
  return "verify";
}

function companyDiscoverySourceLabel(company: Company) {
  if (company.company_discovery_source) return company.company_discovery_source;
  if (company.discovery_role_count > 0) return "Role discovery";
  return "Unknown";
}

function CompanyFitScore({ company }: { company: Company }) {
  const evaluationLabel = companyEvaluationStatusLabel(company);
  if (company.company_evaluation_status === "pending") {
    return <span className="company-fit-score unknown" title={evaluationLabel}>Queued</span>;
  }
  if (company.company_evaluation_status === "evaluating") {
    return <span className="company-fit-score unknown" title={evaluationLabel}>Working</span>;
  }
  if (company.company_evaluation_status === "failed") {
    return <span className="company-fit-score low" title={company.company_evaluation_error || evaluationLabel}>Error</span>;
  }
  const score = Number(company.company_fit_score || 0);
  const tone = !score ? "unknown" : score >= 70 ? "strong" : score >= 55 ? "consider" : "low";
  return <span className={`company-fit-score ${tone}`} title={evaluationLabel}>{score || "—"}</span>;
}

function CompanyLocationFit({ company }: { company: Company }) {
  const label = companyLocationFitLabel(company.company_location_fit);
  return (
    <div className={`company-location-fit ${company.company_location_fit ? "verified" : "verify"}`}>
      <strong>{label}</strong>
      <span>{company.company_location || company.company_remote_policy || "Evidence needed"}</span>
    </div>
  );
}

function companyLocationFitLabel(value: string) {
  if (value === "us-remote") return "Remote eligible";
  if (value === "metro-area" || value === "twin-cities") return "On-site eligible";
  if (value === "both") return "Remote + on-site";
  return "Verify";
}

function compareDiscoveryCompanyRows(left: Company, right: Company, sort: SortState<CompanyDiscoverySortKey>) {
  let result = 0;
  if (sort.key === "company") result = compareText(left.name, right.name, sort.direction);
  if (sort.key === "fit") result = compareNumber(left.company_fit_score || 0, right.company_fit_score || 0, sort.direction);
  if (sort.key === "size") result = compareText(left.company_size, right.company_size, sort.direction);
  if (sort.key === "location") result = compareText(companyLocationFitLabel(left.company_location_fit), companyLocationFitLabel(right.company_location_fit), sort.direction);
  return result || compareText(left.name, right.name, "asc") || compareText(left.id, right.id, "asc");
}

function toggleSelected(values: string[], value: string) {
  return values.includes(value) ? values.filter(item => item !== value) : [...values, value];
}

function storedDiscoveryPreference(key: string, fallback: string) {
  try {
    return window.localStorage.getItem(key) || fallback;
  } catch {
    return fallback;
  }
}

function storeDiscoveryPreference(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // The current search still uses the value when browser storage is unavailable.
  }
}

function CompaniesTable({
  checkingCompanyId,
  onCheck,
  onSort,
  onTrack,
  onUntrack,
  rows,
  sort
}: {
  checkingCompanyId: string;
  onCheck: (company: Company) => Promise<void>;
  onSort: (key: CompanySortKey, initialDirection: SortDirection) => void;
  onTrack: (company: Company) => Promise<void>;
  onUntrack: (company: Company) => void;
  rows: Company[];
  sort: SortState<CompanySortKey>;
}) {
  return (
    <div className="table-scroll companies-table-scroll">
      <table className="simple-table">
        <thead>
          <tr>
            <SortableHeader activeKey={sort.key} direction={sort.direction} label="Company" onSort={onSort} sortKey="company" />
            <SortableHeader activeKey={sort.key} direction={sort.direction} label="Interest" onSort={onSort} sortKey="interest" />
            <SortableHeader activeKey={sort.key} direction={sort.direction} label="Careers URL" onSort={onSort} sortKey="careers_url" />
            <SortableHeader activeKey={sort.key} direction={sort.direction} label="Location fit" onSort={onSort} sortKey="location" />
            <SortableHeader activeKey={sort.key} direction={sort.direction} initialDirection="desc" label="Last check" onSort={onSort} sortKey="last_check" />
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(company => (
            <tr key={company.id} data-company-id={company.id}>
              <td className="role-cell"><Link className="row-select" to={routes.companyDetail(company.id)}><strong>{company.name}</strong><span>{companyMetadataSummary(company) || company.aliases || company.id}</span></Link></td>
              <td>{titleCase(company.interest_status)}</td>
              <td>{company.careers_url ? <a href={company.careers_url} target="_blank" rel="noreferrer">Open</a> : "None"}</td>
              <td><CompanyLocationFit company={company} /></td>
              <td><LastCheckCell company={company} /></td>
              <td>
                <div className="company-table-actions">
                  <button
                    className="button compact table-action-button"
                    type="button"
                    disabled={(company.tracking_status === "tracked" && !company.careers_url) || Boolean(checkingCompanyId)}
                    onClick={() => company.tracking_status === "tracked" ? onCheck(company) : onTrack(company)}
                    aria-label={company.tracking_status === "tracked" ? `Check careers page for ${company.name}` : `Track ${company.name}`}
                  >
                    {company.tracking_status === "tracked" ? <SearchIcon size={16} /> : <ListIcon size={16} />}
                    {checkingCompanyId === company.id
                      ? company.tracking_status === "tracked" ? "Checking" : "Tracking"
                      : company.tracking_status === "tracked" ? "Check" : "Track"}
                  </button>
                  {company.tracking_status === "tracked" ? (
                    <button
                      className="button compact secondary"
                      type="button"
                      disabled={Boolean(checkingCompanyId)}
                      onClick={() => onUntrack(company)}
                      aria-label={`Move ${company.name} back to Discovery`}
                    >
                      Untrack
                    </button>
                  ) : null}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="empty-state" style={{ display: rows.length ? "none" : "block" }}>No companies match the current filters.</div>
    </div>
  );
}

function compareCompanyRows(left: Company, right: Company, sort: SortState<CompanySortKey>) {
  let result = 0;
  if (sort.key === "company") result = compareText(left.name, right.name, sort.direction);
  if (sort.key === "interest") result = compareText(left.interest_status, right.interest_status, sort.direction);
  if (sort.key === "careers_url") result = compareText(left.careers_url, right.careers_url, sort.direction);
  if (sort.key === "location") result = compareText(companyLocationFitLabel(left.company_location_fit), companyLocationFitLabel(right.company_location_fit), sort.direction);
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
  valueLabels = {},
  selected,
  onChange
}: {
  label: string;
  values: string[];
  valueLabels?: Record<string, string>;
  selected: string[];
  onChange: (values: string[]) => void;
}) {
  const allSelected = values.length === selected.length;
  const displayValue = (value: string) => valueLabels[value] || titleCase(value);
  const summary = allSelected ? "All" : selected.length === 1 ? displayValue(selected[0]) : `${selected.length} selected`;

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
            {displayValue(value)}
          </label>
        ))}
      </div>
    </details>
  );
}

export function CompanyDetailPage({ data: shellData, refresh, applyCompanyCandidateUpdates, createNew = false }: CompanyDetailPageProps) {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const isNewCompany = createNew || id === "new";
  const queryClient = useQueryClient();
  const companyDetailQuery = useCompanyDetail(id, !isNewCompany && Boolean(id));
  const companyCandidatesQuery = useCompanyCandidateList({ companyId: id }, !isNewCompany && Boolean(id));
  const discoveryCandidatesQuery = useDiscoveryCandidateList({ companyId: id }, {}, !isNewCompany && Boolean(id));
  const data = useMemo<AppState>(() => {
    const detailItem = companyDetailQuery.data?.item;
    const { company_career_source: careerSource, ...detailedCompany } = detailItem || { company_career_source: null };
    return {
      ...shellData,
      companies: detailItem
        ? shellData.companies.map(company => company.id === id ? { ...company, ...detailedCompany } : company)
        : shellData.companies,
      company_career_sources: detailItem
        ? [
            ...shellData.company_career_sources.filter(source => source.company_id !== id),
            ...(careerSource ? [careerSource] : [])
          ]
        : shellData.company_career_sources,
      company_posting_candidates: (companyCandidatesQuery.data?.pages || [])
        .flatMap(page => page.items.map(companyListItemToLegacyCandidate)),
      discovery_candidates: (discoveryCandidatesQuery.data?.pages || [])
        .flatMap(page => page.items.map(discoveryListItemToLegacyCandidate))
    };
  }, [companyCandidatesQuery.data?.pages, companyDetailQuery.data?.item, discoveryCandidatesQuery.data?.pages, id, shellData]);
  const refreshCandidatePool = async (pool: "company" | "discovery") => {
    const [next] = await Promise.all([
      refresh(),
      queryClient.invalidateQueries({ queryKey: readModelQueryKeys.candidateLists(pool) })
    ]);
    return next;
  };
  const patchCompanyDetail = (companyUpdate: Partial<Company>) => {
    queryClient.setQueryData<EntityDetail<"company">>(
      readModelQueryKeys.entityDetail("company", companyUpdate.id || id),
      current => current ? {
        ...current,
        item: { ...current.item, ...companyUpdate }
      } : current
    );
  };
  const company = isNewCompany ? null : data.companies.find(row => row.id === id) || null;
  const invalidCompany = !isNewCompany && !company;
  const [operationStatus, setOperationStatus] = useState("");
  const [activeCandidateActionId, setActiveCandidateActionId] = useState("");
  const [isCheckingCareers, setIsCheckingCareers] = useState(false);
  const [isResearching, setIsResearching] = useState(false);
  const [isTracking, setIsTracking] = useState(false);
  const [untrackDialogOpen, setUntrackDialogOpen] = useState(false);
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
      patchCompanyDetail(result.company);
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
      const next = await refreshCandidatePool("company");
      const refreshedCompany = next.companies.find(item => item.id === company.id);
      if (refreshedCompany) patchCompanyDetail({
        id: refreshedCompany.id,
        last_checked_at: refreshedCompany.last_checked_at,
        last_check_status: refreshedCompany.last_check_status
      });
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
      patchCompanyDetail(result.company);
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
      const result = await trackCompany(company.id);
      patchCompanyDetail(result.company);
      await refresh();
      setOperationStatus("Company is now tracked. Hunter can use its careers URL in Companies mode.");
    } catch (error) {
      setOperationStatus(`Could not track company. ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setIsTracking(false);
    }
  }

  async function untrackCurrentCompany() {
    if (!company || company.tracking_status !== "tracked" || isTracking) return;
    setIsTracking(true);
    setOperationStatus("Moving company back to Discovery...");
    try {
      const result = await untrackCompany(company.id);
      patchCompanyDetail(result.company);
      await refresh();
      setOperationStatus("Company moved to Discovery. Existing company data was kept.");
    } catch (error) {
      setOperationStatus(`Could not stop tracking company. ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setIsTracking(false);
      setUntrackDialogOpen(false);
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
      const result = await upsertCompany(company.id, { interest_status: interestStatus });
      patchCompanyDetail(result.company);
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
      const result = await resolveCompanyMetadataSuggestion(company.id, suggestion.id, action);
      patchCompanyDetail(result.company);
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
      const result = await archiveCompany(company.id);
      patchCompanyDetail(result.company);
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
      const result = await restoreCompany(company.id, "neutral");
      patchCompanyDetail(result.company);
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
      const result = await updateCompanyCandidate(candidateId, "ignored");
      applyCompanyCandidateUpdates([result.candidate]);
      setOperationStatus("Candidate ignored.");
    } catch (error) {
      setOperationStatus(`Could not ignore candidate. ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setActiveCandidateActionId("");
    }
  }

  async function pursueCandidate(candidateId: string) {
    if (activeCandidateActionId) return;
    setActiveCandidateActionId(candidateId);
    setOperationStatus("Adding role to Considering...");
    try {
      await pursueCompanyCandidate(candidateId);
      await refreshCandidatePool("company");
      setOperationStatus("Role added to Considering.");
    } catch (error) {
      setOperationStatus(`Could not add role to Considering. ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setActiveCandidateActionId("");
    }
  }

  async function updateDiscoveryRole(candidateId: string, action: "ignored" | "pursued") {
    if (activeCandidateActionId) return;
    setActiveCandidateActionId(candidateId);
    setOperationStatus(`${action === "ignored" ? "Ignoring" : "Pursuing"} Discovery role...`);
    try {
      if (action === "ignored") await updateDiscoveryCandidate(candidateId, "ignored");
      else await pursueDiscoveryCandidate(candidateId);
      await refreshCandidatePool("discovery");
      setOperationStatus(action === "ignored" ? "Discovery role ignored." : "Discovery role added to Considering.");
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

  if (!isNewCompany && companyDetailQuery.isPending) {
    return <div className="empty-state" style={{ display: "block" }}>Loading company details…</div>;
  }

  if (!isNewCompany && companyDetailQuery.error) {
    return <div className="empty-state" style={{ display: "block" }}>Could not load company details. {companyDetailQuery.error.message}</div>;
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
          ) : (
            <button className="button" type="button" disabled={isTracking} onClick={() => setUntrackDialogOpen(true)}>
              {isTracking ? "Moving…" : "Move to Discovery"}
            </button>
          )}
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
          <span>Needs decision</span><strong>{candidateRows.filter(row => row.candidate.status === "new").length}</strong><small>Across both role sources</small>
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
                  ? row.candidate.status !== "pursued" && row.candidate.processing_status === "ready"
                  : row.candidate.status !== "pursued";
                const canIgnore = row.source === "discovery"
                  ? row.candidate.status === "new"
                  : row.candidate.status !== "ignored" && row.candidate.status !== "pursued";
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
                      <div><dt>Status</dt><dd>{candidate.status === "pursued" ? "Considering" : titleCase(candidate.status)}</dd></div>
                      <div><dt>Fit</dt><dd>{candidate.fit_score ? `${candidate.fit_score} · ${fitLabel(candidate.fit_score)}` : "Not scored"}</dd></div>
                      <div><dt>Seen</dt><dd>{candidateDateLabel(candidate)}</dd></div>
                    </dl>
                    <div className="company-candidate-actions">
                      <a className="button compact" href={sourceUrl} target="_blank" rel="noreferrer">Open source</a>
                      <button
                        className="button compact primary"
                        type="button"
                        disabled={!canIngest || Boolean(activeCandidateActionId)}
                        onClick={() => isDiscovery ? updateDiscoveryRole(candidate.id, "pursued") : pursueCandidate(candidate.id)}
                      >
                        {isActiveAction ? "Saving..." : candidate.status === "pursued" ? "Considering" : "Consider"}
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
                  ? companyCandidatesQuery.hasNextPage || discoveryCandidatesQuery.hasNextPage
                    ? "No loaded candidates match this search. Load more linked roles to continue."
                    : "No candidates match this search."
                  : "No candidates have been recorded for this company yet."}
              </div>}
            </div>
            {companyCandidatesQuery.hasNextPage || discoveryCandidatesQuery.hasNextPage ? (
              <div className="candidate-load-more">
                <button
                  className="button"
                  type="button"
                  disabled={companyCandidatesQuery.isFetchingNextPage || discoveryCandidatesQuery.isFetchingNextPage}
                  onClick={() => {
                    if (companyCandidatesQuery.hasNextPage) void companyCandidatesQuery.fetchNextPage();
                    if (discoveryCandidatesQuery.hasNextPage) void discoveryCandidatesQuery.fetchNextPage();
                  }}
                >
                  {companyCandidatesQuery.isFetchingNextPage || discoveryCandidatesQuery.isFetchingNextPage
                    ? "Loading…"
                    : "Load more linked roles"}
                </button>
              </div>
            ) : null}
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
          {company.company_fit_checked_at ? (
            <article className="panel company-rail-panel company-discovery-detail">
              <div className="company-section-header compact">
                <div><h2>Company discovery</h2><p>Source-backed company fit before role review.</p></div>
                <CompanyFitScore company={company} />
              </div>
              <p className="company-discovery-detail-summary">{company.company_fit_summary || "Profile fit needs review."}</p>
              <dl className="company-detail-list">
                <div><dt>Source</dt><dd>{company.company_discovery_source || "Unknown"}</dd></div>
                <div><dt>Search</dt><dd>{company.company_discovery_query || "Not recorded"}</dd></div>
                <div><dt>Location fit</dt><dd>{companyLocationFitLabel(company.company_location_fit)}</dd></div>
                <div><dt>Location</dt><dd>{company.company_location || "Not recorded"}</dd></div>
                <div><dt>Remote policy</dt><dd>{company.company_remote_policy || "Not recorded"}</dd></div>
                <div><dt>Checked</dt><dd>{dateOnlyLabel(company.company_fit_checked_at)}</dd></div>
              </dl>
              {company.company_discovery_evidence ? <p className="company-discovery-detail-evidence">{company.company_discovery_evidence}</p> : null}
              {company.company_location_evidence ? <p className="company-discovery-detail-evidence"><strong>Location evidence:</strong> {company.company_location_evidence}</p> : null}
              {company.company_discovery_source_url ? (
                <a className="button compact" href={company.company_discovery_source_url} target="_blank" rel="noreferrer">
                  <ExternalIcon size={14} /> Open discovery source
                </a>
              ) : null}
            </article>
          ) : null}
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
      {untrackDialogOpen && company.tracking_status === "tracked" ? (
        <CompanyUntrackConfirmationModal
          company={company}
          isConfirming={isTracking}
          onClose={() => { if (!isTracking) setUntrackDialogOpen(false); }}
          onConfirm={untrackCurrentCompany}
        />
      ) : null}
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
