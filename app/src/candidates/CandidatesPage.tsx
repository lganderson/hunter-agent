import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ExternalIcon, FilterIcon, SearchIcon } from "../components/Icons";
import { checkAllCompanyPostings, ingestCompanyCandidate, updateCompanyCandidate } from "../core/api";
import { dateOnlyLabel, titleCase } from "../core/format";
import { routes } from "../core/routes";
import type { AppState, Company, CompanyPostingCandidate } from "../core/types";
import {
  CANDIDATE_FILTERS,
  RECOMMENDED_FIT_SCORE,
  STRONG_FIT_SCORE,
  candidateFitScore,
  candidateMatchesFilter,
  fitBand,
  isCurrentNewCandidate,
  isRecommendedCandidate,
  type CandidateFilter
} from "../companies/candidateUtils";

type CandidateReviewPageProps = {
  data: AppState;
  refresh: () => Promise<AppState>;
};

type CandidateRow = {
  candidate: CompanyPostingCandidate;
  company: Company | null;
  fitScore: number;
  latestCheckAt: string;
};

const INTEREST_VALUES = ["interested", "neutral", "archived"];
const FIT_VALUES = ["all", "strong", "recommended", "low"];
const SORT_VALUES = ["fit", "last_seen", "company", "title"];

export function CandidatesPage({ data, refresh }: CandidateReviewPageProps) {
  const [search, setSearch] = useState("");
  const [candidateFilter, setCandidateFilter] = useState<CandidateFilter>("recommended");
  const [interestStatuses, setInterestStatuses] = useState<string[]>(INTEREST_VALUES);
  const [companyIds, setCompanyIds] = useState<string[]>([]);
  const [fitFilter, setFitFilter] = useState("all");
  const [latestOnly, setLatestOnly] = useState(false);
  const [sortBy, setSortBy] = useState("fit");
  const [operationStatus, setOperationStatus] = useState("");
  const [checkingAll, setCheckingAll] = useState(false);

  const companyById = useMemo(
    () => new Map(data.companies.map(company => [company.id, company])),
    [data.companies]
  );
  const companyOptions = useMemo(
    () => data.companies
      .filter(company => data.company_posting_candidates.some(candidate => candidate.company_id === company.id))
      .sort((a, b) => a.name.localeCompare(b.name)),
    [data.companies, data.company_posting_candidates]
  );

  useEffect(() => {
    setCompanyIds(previous => {
      const validIds = new Set(companyOptions.map(company => company.id));
      const selected = previous.filter(id => validIds.has(id));
      return selected.length ? selected : companyOptions.map(company => company.id);
    });
  }, [companyOptions]);

  const allRows = useMemo<CandidateRow[]>(
    () => data.company_posting_candidates.map(candidate => {
      const company = companyById.get(candidate.company_id) || null;
      return {
        candidate,
        company,
        fitScore: candidateFitScore(candidate),
        latestCheckAt: company?.last_checked_at || ""
      };
    }),
    [companyById, data.company_posting_candidates]
  );

  const rowsBeforeStatus = useMemo(
    () => allRows.filter(row => {
      const { candidate, company, fitScore } = row;
      const query = search.trim().toLowerCase();
      if (company && !matchesSelection(company.interest_status, interestStatuses, INTEREST_VALUES)) return false;
      if (!company && interestStatuses.length !== INTEREST_VALUES.length) return false;
      if (!matchesSelection(candidate.company_id, companyIds, companyOptions.map(item => item.id))) return false;
      if (latestOnly && !isCurrentNewCandidate(candidate, row.latestCheckAt)) return false;
      if (!matchesFitFilter(fitScore, fitFilter)) return false;
      if (query) {
        const haystack = [
          candidate.id,
          candidate.title,
          candidate.url,
          candidate.status,
          candidate.fit_score,
          candidate.fit_summary,
          candidate.first_seen_at,
          candidate.last_seen_at,
          company?.id || "",
          company?.name || "",
          company?.interest_status || "",
          company?.careers_url || ""
        ].join(" ").toLowerCase();
        if (!haystack.includes(query)) return false;
      }
      return true;
    }),
    [allRows, companyIds, companyOptions, fitFilter, interestStatuses, latestOnly, search]
  );

  const candidateCounts = useMemo(
    () => ({
      recommended: rowsBeforeStatus.filter(row => isRecommendedCandidate(row.candidate, row.latestCheckAt)).length,
      new: rowsBeforeStatus.filter(row => isCurrentNewCandidate(row.candidate, row.latestCheckAt)).length,
      all: rowsBeforeStatus.length,
      ignored: rowsBeforeStatus.filter(row => row.candidate.status === "ignored").length,
      ingested: rowsBeforeStatus.filter(row => row.candidate.status === "ingested").length,
      unavailable: rowsBeforeStatus.filter(row => row.candidate.status === "unavailable").length
    }),
    [rowsBeforeStatus]
  );

  const rows = useMemo(
    () => rowsBeforeStatus
      .filter(row => candidateMatchesFilter(row.candidate, candidateFilter, row.latestCheckAt))
      .sort((a, b) => compareCandidateRows(a, b, sortBy)),
    [candidateFilter, rowsBeforeStatus, sortBy]
  );

  async function setCandidateStatus(candidateId: string, status: string) {
    setOperationStatus(status === "ignored" ? "Ignoring candidate..." : "Updating candidate...");
    try {
      await updateCompanyCandidate(candidateId, status);
      await refresh();
      setOperationStatus(status === "ignored" ? "Candidate ignored." : "Candidate returned to New.");
    } catch (error) {
      setOperationStatus(`Could not update candidate. ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  async function ingestCandidate(candidateId: string) {
    setOperationStatus("Ingesting candidate...");
    try {
      await ingestCompanyCandidate(candidateId);
      await refresh();
      setOperationStatus("Candidate ingested.");
    } catch (error) {
      setOperationStatus(`Could not ingest candidate. ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  async function checkAllCompanies() {
    setCheckingAll(true);
    setOperationStatus("Checking careers pages for all companies...");
    try {
      const result = await checkAllCompanyPostings();
      await refresh();
      const errorText = result.error_count ? ` ${result.error_count} failed.` : "";
      const detailChecked = result.verification_count ? ` ${result.verification_count} detail checked.` : "";
      const detailSkipped = result.verification_skipped_count ? ` ${result.verification_skipped_count} detail skipped.` : "";
      setOperationStatus(
        `Checked ${result.checked_count} companies. ${result.new_count} new candidates, ${result.recommended_count} recommended, ${result.unavailable_count} unavailable. ${result.skipped_count} skipped.${detailChecked}${detailSkipped}${errorText}`
      );
    } catch (error) {
      setOperationStatus(`Could not check all companies. ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setCheckingAll(false);
    }
  }

  function clearFilters() {
    setSearch("");
    setCandidateFilter("recommended");
    setInterestStatuses(INTEREST_VALUES);
    setCompanyIds(companyOptions.map(company => company.id));
    setFitFilter("all");
    setLatestOnly(false);
    setSortBy("fit");
  }

  return (
    <section className="view-section" id="candidates-view" aria-label="Posting candidates">
      <article className="panel">
        <div className="toolbar" aria-label="Candidate filters">
          <label className="search">
            <span className="sr-only">Search posting candidates</span>
            <SearchIcon />
            <input value={search} onChange={event => setSearch(event.target.value)} type="search" placeholder="Search candidates, companies, fit notes..." />
          </label>
          <MultiFilter label="Interest" values={INTEREST_VALUES} selected={interestStatuses} onChange={setInterestStatuses} />
          <MultiFilter label="Company" values={companyOptions.map(company => company.id)} selected={companyIds} onChange={setCompanyIds} labelForValue={id => companyById.get(id)?.name || id} />
          <label className="filter">Fit <select value={fitFilter} onChange={event => setFitFilter(event.target.value)}>
            {FIT_VALUES.map(value => <option key={value} value={value}>{fitFilterLabel(value)}</option>)}
          </select></label>
          <label className="filter">Sort <select value={sortBy} onChange={event => setSortBy(event.target.value)}>
            {SORT_VALUES.map(value => <option key={value} value={value}>{sortLabel(value)}</option>)}
          </select></label>
          <label className="toggle"><input checked={latestOnly} onChange={event => setLatestOnly(event.target.checked)} type="checkbox" /> Latest scan</label>
          <button className="button" type="button" onClick={clearFilters}><FilterIcon size={16} /> Clear</button>
          <button className="button primary" type="button" disabled={checkingAll} onClick={checkAllCompanies}>
            {checkingAll ? "Checking..." : "Check All Careers"}
          </button>
        </div>

        <div className="candidate-filter-bar aggregate" aria-label="Candidate status filters">
          {CANDIDATE_FILTERS.map(filter => (
            <button
              className={candidateFilter === filter.id ? "candidate-filter active" : "candidate-filter"}
              key={filter.id}
              type="button"
              onClick={() => setCandidateFilter(filter.id)}
            >
              {filter.label}
              <span>{candidateCounts[filter.id]}</span>
            </button>
          ))}
        </div>

        {operationStatus ? <div className="table-operation-status">{operationStatus}</div> : null}
        <div className="candidate-review-summary">
          <strong>{rows.length}</strong>
          <span>shown from {data.company_posting_candidates.length} total candidates</span>
        </div>

        <div className="table-scroll">
          <table className="simple-table candidates-table">
            <thead>
              <tr>
                <th>Candidate</th>
                <th>Company</th>
                <th>Fit</th>
                <th>Status</th>
                <th>Last seen</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(({ candidate, company, latestCheckAt }) => (
                <tr key={candidate.id}>
                  <td className="role-cell candidate-title-cell">
                    <strong>{candidate.title || candidate.url}</strong>
                  </td>
                  <td>
                    {company ? <Link to={routes.companyDetail(company.id)}>{company.name}</Link> : candidate.company_id || "Unknown"}
                    <span className="cell-subtle">{company ? titleCase(company.interest_status) : "No company record"}</span>
                  </td>
                  <td className="candidate-score-cell">
                    <span className={`pill fit-${fitBand(candidate)}`}>{candidate.fit_score || "0"}</span>
                  </td>
                  <td>{titleCase(candidate.status)}</td>
                  <td>
                    {candidateDateLabel(candidate)}
                  </td>
                  <td>
                    <div className="table-actions">
                      <a className="button compact" href={candidate.url} target="_blank" rel="noreferrer"><ExternalIcon size={15} /> Open</a>
                      <button className="button compact" type="button" disabled={candidate.status === "ingested"} onClick={() => ingestCandidate(candidate.id)}>Ingest</button>
                      {candidate.status === "ignored"
                        ? <button className="button compact" type="button" onClick={() => setCandidateStatus(candidate.id, "new")}>Mark New</button>
                        : <button className="button compact" type="button" disabled={candidate.status === "ingested"} onClick={() => setCandidateStatus(candidate.id, "ignored")}>Ignore</button>}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="empty-state" style={{ display: rows.length ? "none" : "block" }}>No posting candidates match the current filters.</div>
        </div>
      </article>
    </section>
  );
}

function MultiFilter({
  label,
  values,
  selected,
  onChange,
  labelForValue = titleCase
}: {
  label: string;
  values: string[];
  selected: string[];
  onChange: (values: string[]) => void;
  labelForValue?: (value: string) => string;
}) {
  const allSelected = values.length === selected.length;
  const summary = allSelected ? "All" : selected.length === 1 ? labelForValue(selected[0]) : `${selected.length} selected`;

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
            {labelForValue(value)}
          </label>
        ))}
      </div>
    </details>
  );
}

function compareCandidateRows(left: CandidateRow, right: CandidateRow, sortBy: string) {
  if (sortBy === "last_seen") return candidateDate(right).localeCompare(candidateDate(left)) || right.fitScore - left.fitScore;
  if (sortBy === "company") return (left.company?.name || "").localeCompare(right.company?.name || "") || right.fitScore - left.fitScore;
  if (sortBy === "title") return (left.candidate.title || "").localeCompare(right.candidate.title || "") || right.fitScore - left.fitScore;
  return right.fitScore - left.fitScore || candidateDate(right).localeCompare(candidateDate(left));
}

function candidateDate(row: CandidateRow) {
  return row.candidate.last_seen_at || row.candidate.first_seen_at || "";
}

function candidateDateLabel(candidate: CompanyPostingCandidate) {
  const value = candidate.last_seen_at || candidate.first_seen_at || "";
  return value ? dateOnlyLabel(value) : "Not checked";
}

function matchesSelection(value: string, selected: string[], values: string[]) {
  if (!values.length || selected.length === values.length) return true;
  return selected.includes(value);
}

function matchesFitFilter(score: number, filter: string) {
  if (filter === "strong") return score >= STRONG_FIT_SCORE;
  if (filter === "recommended") return score >= RECOMMENDED_FIT_SCORE;
  if (filter === "low") return score < RECOMMENDED_FIT_SCORE;
  return true;
}

function fitFilterLabel(value: string) {
  if (value === "strong") return "Strong";
  if (value === "recommended") return "45+";
  if (value === "low") return "Low";
  return "All";
}

function sortLabel(value: string) {
  if (value === "last_seen") return "Last seen";
  if (value === "company") return "Company";
  if (value === "title") return "Title";
  return "Fit";
}
