import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";
import { ExternalIcon, FilterIcon, ListIcon, SearchIcon } from "../components/Icons";
import {
  archiveCompany,
  checkCompanyPostings,
  ingestCompanyCandidate,
  linkCompanyContact,
  restoreCompany,
  unlinkCompanyContact,
  updateCompanyCandidate,
  upsertCompany
} from "../core/api";
import { routes } from "../core/routes";
import { titleCase } from "../core/format";
import type { AppState, Company, CompanyCareerSource, CompanyPostingCandidate } from "../core/types";
import {
  CANDIDATE_FILTERS,
  RECOMMENDED_CANDIDATE_LIMIT,
  candidateEmptyMessage,
  candidateFitScore,
  candidateMatchesFilter,
  candidateRank,
  fitBand,
  isCurrentNewCandidate,
  isRecommendedCandidate,
  type CandidateFilter
} from "./candidateUtils";

type CompaniesPageProps = {
  data: AppState;
  refresh: () => Promise<AppState>;
};

type CompanyDetailPageProps = CompaniesPageProps & {
  createNew?: boolean;
};

const INTEREST_STATUSES = ["all", "interested", "neutral", "archived"];

export function CompaniesPage({ data, refresh }: CompaniesPageProps) {
  const [search, setSearch] = useState("");
  const [interestStatus, setInterestStatus] = useState("all");
  const [checkingCompanyId, setCheckingCompanyId] = useState("");
  const [operationStatus, setOperationStatus] = useState("");

  const rows = useMemo(() => {
    const query = search.toLowerCase();
    return data.companies
      .filter(company => {
        if (interestStatus !== "all" && company.interest_status !== interestStatus) return false;
        if (!query) return true;
        return [
          company.id,
          company.name,
          company.aliases,
          company.interest_status,
          company.website,
          company.careers_url,
          company.notes,
          company.last_check_status
        ].join(" ").toLowerCase().includes(query);
      })
      .sort((a, b) => interestRank(a.interest_status) - interestRank(b.interest_status) || a.name.localeCompare(b.name));
  }, [data.companies, interestStatus, search]);

  async function checkCareersFromTable(company: Company) {
    if (!company.careers_url || checkingCompanyId) return;
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

  return (
    <section className="view-section" id="companies-view" aria-label="Companies">
      <article className="panel">
        <div className="toolbar" aria-label="Company tools">
          <label className="search">
            <span className="sr-only">Search companies</span>
            <SearchIcon />
            <input value={search} onChange={event => setSearch(event.target.value)} type="search" placeholder="Search companies, careers URLs, notes..." />
          </label>
          <label className="filter">Interest <select value={interestStatus} onChange={event => setInterestStatus(event.target.value)}>
            {INTEREST_STATUSES.map(status => <option key={status} value={status}>{titleCase(status)}</option>)}
          </select></label>
          <Link className="button primary" to={routes.companyNew}><ListIcon /> New Company</Link>
        </div>
        {operationStatus ? <div className="table-operation-status">{operationStatus}</div> : null}
        <div className="table-scroll">
          <table className="simple-table">
            <thead>
              <tr>
                <th>Company</th>
                <th>Interest</th>
                <th>Careers URL</th>
                <th>Last check</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(company => (
                <tr key={company.id} data-company-id={company.id}>
                  <td className="role-cell"><Link className="row-select" to={routes.companyDetail(company.id)}><strong>{company.name}</strong><span>{company.aliases || company.id}</span></Link></td>
                  <td>{titleCase(company.interest_status)}</td>
                  <td>{company.careers_url ? <a href={company.careers_url} target="_blank" rel="noreferrer">Open</a> : "None"}</td>
                  <td><LastCheckCell company={company} /></td>
                  <td>
                    <button
                      className="button compact table-action-button"
                      type="button"
                      disabled={!company.careers_url || Boolean(checkingCompanyId)}
                      onClick={() => checkCareersFromTable(company)}
                      aria-label={`Check careers page for ${company.name}`}
                    >
                      <SearchIcon size={16} />
                      {checkingCompanyId === company.id ? "Checking" : "Check"}
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

export function CompanyDetailPage({ data, refresh, createNew = false }: CompanyDetailPageProps) {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const isNewCompany = createNew || id === "new";
  const company = isNewCompany ? null : data.companies.find(row => row.id === id) || null;
  const invalidCompany = !isNewCompany && !company;
  const [operationStatus, setOperationStatus] = useState("");
  const [activeCandidateActionId, setActiveCandidateActionId] = useState("");

  const linkedContactIds = useMemo(
    () => new Set(data.company_contacts.filter(link => link.company_id === company?.id).map(link => link.contact_id)),
    [company?.id, data.company_contacts]
  );
  const linkedContacts = useMemo(
    () => data.contacts.filter(contact => linkedContactIds.has(contact.id)),
    [data.contacts, linkedContactIds]
  );
  const linkedPostings = useMemo(
    () => data.applications.filter(app => app.company_id === company?.id),
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
  const [candidateFilter, setCandidateFilter] = useState<CandidateFilter>("recommended");
  const recommendedCount = useMemo(
    () => Math.min(candidates.filter(candidate => isRecommendedCandidate(candidate, company?.last_checked_at || "")).length, RECOMMENDED_CANDIDATE_LIMIT),
    [candidates, company?.last_checked_at]
  );
  const candidateCounts = useMemo(
    () => ({
      recommended: recommendedCount,
      new: candidates.filter(candidate => isCurrentNewCandidate(candidate, company?.last_checked_at || "")).length,
      all: candidates.length,
      ignored: candidates.filter(candidate => candidate.status === "ignored").length,
      ingested: candidates.filter(candidate => candidate.status === "ingested").length,
      unavailable: candidates.filter(candidate => candidate.status === "unavailable").length
    }),
    [candidates, company?.last_checked_at, recommendedCount]
  );
  const visibleCandidates = useMemo(
    () => {
      const rows = candidates.filter(candidate => candidateMatchesFilter(candidate, candidateFilter, company?.last_checked_at || ""));
      return candidateFilter === "recommended" ? rows.slice(0, RECOMMENDED_CANDIDATE_LIMIT) : rows;
    },
    [candidateFilter, candidates, company?.last_checked_at]
  );
  const availableContacts = useMemo(
    () => data.contacts.filter(contact => !linkedContactIds.has(contact.id)),
    [data.contacts, linkedContactIds]
  );
  const [contactId, setContactId] = useState(availableContacts[0]?.id || "");

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
    if (!company) return;
    setOperationStatus("Checking careers page...");
    try {
      const result = await checkCompanyPostings(company.id);
      await refresh();
      setCandidateFilter("recommended");
      const detailChecked = result.verification_count ? `; ${result.verification_count} detail checked` : "";
      const detailSkipped = result.verification_skipped_count ? `; ${result.verification_skipped_count} detail skipped` : "";
      setOperationStatus(`Check complete. ${result.new.length} new candidate${result.new.length === 1 ? "" : "s"}; ${result.recommended.length} recommended; ${result.unavailable_count} unavailable${detailChecked}${detailSkipped}.`);
    } catch (error) {
      setOperationStatus(`Could not check careers page. ${error instanceof Error ? error.message : String(error)}`);
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
      setCandidateFilter("ignored");
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
      setCandidateFilter("ingested");
      setOperationStatus("Candidate ingested.");
    } catch (error) {
      setOperationStatus(`Could not ingest candidate. ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setActiveCandidateActionId("");
    }
  }

  if (invalidCompany) return <Navigate to={routes.companies} replace />;

  return (
    <section className="view-section company-detail-page" aria-label={company ? company.name : "New Company"}>
      <article className="panel company-detail-panel">
        <div className="detail-topline">
          <div>
            <h2 id="company-form-title">{company ? company.name || "Unnamed company" : "New Company"}</h2>
            <p>{company ? [company.interest_status ? titleCase(company.interest_status) : "", company.last_check_status || "Not checked"].filter(Boolean).join(" · ") : "Create a managed company record."}</p>
          </div>
          <div className="detail-actions">
            <Link className="button compact" to={routes.companies}>Back to Companies</Link>
            {company
              ? company.interest_status === "archived"
                ? <button className="button compact" type="button" onClick={restoreCurrentCompany}>Restore</button>
                : <button className="button compact" type="button" onClick={archiveCurrentCompany}>Archive</button>
              : null}
          </div>
        </div>
        <form className="management-form" onSubmit={saveCompany} key={company?.id || "new-company"}>
          <label className="form-field full">Name <input name="name" type="text" required defaultValue={company?.name || ""} autoFocus /></label>
          <label className="form-field">Interest <select name="interest_status" defaultValue={company?.interest_status || "neutral"}>
            <option value="interested">Interested</option>
            <option value="neutral">Neutral</option>
            <option value="archived">Archived</option>
          </select></label>
          <label className="form-field">Aliases <input name="aliases" type="text" defaultValue={company?.aliases || ""} /></label>
          <label className="form-field">Website <input name="website" type="url" defaultValue={company?.website || ""} /></label>
          <label className="form-field">Careers URL <input name="careers_url" type="url" defaultValue={company?.careers_url || ""} /></label>
          <label className="form-field full">Notes <textarea name="notes" defaultValue={company?.notes || ""} /></label>
          <div className="detail-actions form-field full">
            <button className="button primary" type="submit"><FilterIcon size={16} /> Save Company</button>
            <button className="button" type="button" disabled={!company || !company.careers_url} onClick={checkCareers}><SearchIcon size={16} /> Check Careers</button>
            {company?.careers_url ? <a className="button" href={company.careers_url} target="_blank" rel="noreferrer"><ExternalIcon size={16} /> Careers</a> : null}
          </div>
        </form>
        <div className="detail-status">{operationStatus}</div>

        <div className="panel-header"><h2 className="panel-title">Career Source</h2></div>
        <div className="source-summary">
          {careerSource
            ? (
              <>
                <div className="source-summary-grid">
                  <div>
                    <span>Platform</span>
                    <strong>{titleCase(careerSource.platform_type.replaceAll("_", " "))}</strong>
                  </div>
                  <div>
                    <span>Status</span>
                    <strong>{titleCase(careerSource.status || "discovered")}</strong>
                  </div>
                  <div>
                    <span>Last verified</span>
                    <strong>{careerSource.last_verified_at || "Not verified"}</strong>
                  </div>
                </div>
                {careerSourceEvidence.length ? (
                  <ul className="source-evidence">
                    {careerSourceEvidence.map(item => <li key={item}>{item}</li>)}
                  </ul>
                ) : null}
              </>
            )
            : <div className="empty-state" style={{ display: "block" }}>No career source has been discovered yet. Check careers to inspect and save one.</div>}
        </div>

        <div className="panel-header"><h2 className="panel-title">Associated Contacts</h2></div>
        <div className="management-form">
          <label className="form-field full">Contact <select value={contactId} onChange={event => setContactId(event.target.value)} disabled={!availableContacts.length}>
            {availableContacts.map(contact => <option key={contact.id} value={contact.id}>{contact.name || contact.id} · {contact.role || "No role"}</option>)}
          </select></label>
          <div className="detail-actions form-field full">
            <button className="button" type="button" disabled={!company || !contactId} onClick={addContact}><ListIcon /> Link Contact</button>
          </div>
        </div>
        <div className="association-list">
          {company
            ? linkedContacts.length
              ? linkedContacts.map(contact => (
                <div className="association-row" key={contact.id}>
                  <div>
                    <strong>{contact.name || contact.id}</strong>
                    <span>{[contact.company, contact.role, contact.status].filter(Boolean).join(" · ") || "No details"}</span>
                  </div>
                  <button className="button compact" type="button" onClick={() => removeContact(contact.id)}>Unlink</button>
                </div>
              ))
              : <div className="empty-state" style={{ display: "block" }}>No contacts are associated with this company.</div>
            : <div className="empty-state" style={{ display: "block" }}>Save the company before linking contacts.</div>}
        </div>

        <div className="panel-header"><h2 className="panel-title">Associated Postings</h2></div>
        <div className="association-list">
          {linkedPostings.length
            ? linkedPostings.map(app => (
              <div className="association-row" key={app.id}>
                <div>
                  <strong>{app.role || app.id}</strong>
                  <span>{titleCase(app.stage)}{app.outcome ? ` · ${titleCase(app.outcome)}` : ""}</span>
                </div>
                <Link className="button compact" to={routes.postingDetail(app.id)}>Open</Link>
              </div>
            ))
            : <div className="empty-state" style={{ display: "block" }}>No postings are associated with this company.</div>}
        </div>

        <div className="panel-header"><h2 className="panel-title">Posting Candidates</h2>{recommendedCount ? <span className="panel-kicker">{recommendedCount} recommended</span> : null}</div>
        <div className="candidate-filter-bar" aria-label="Candidate filters">
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
        <div className="association-list">
          {visibleCandidates.length
            ? visibleCandidates.map(candidate => (
              <div className="association-row" key={candidate.id}>
                <div>
                  <strong>{candidate.title || candidate.url}</strong>
                  <div className="candidate-meta">
                    <span>{titleCase(candidate.status)} · {candidate.last_seen_at || candidate.first_seen_at || "Not checked"}</span>
                    {candidate.fit_score ? <span className={`pill fit-${fitBand(candidate)}`}>Fit {candidate.fit_score}</span> : null}
                  </div>
                  {candidate.fit_summary ? <span className="candidate-fit-summary">{candidate.fit_summary}</span> : null}
                </div>
                <div className="detail-actions">
                  <a className="button compact" href={candidate.url} target="_blank" rel="noreferrer">Open</a>
                  <button
                    className="button compact"
                    type="button"
                    disabled={candidate.status === "ingested" || Boolean(activeCandidateActionId)}
                    onClick={() => ingestCandidate(candidate.id)}
                  >
                    {activeCandidateActionId === candidate.id ? "Ingesting..." : "Ingest"}
                  </button>
                  <button
                    className="button compact"
                    type="button"
                    disabled={candidate.status === "ignored" || candidate.status === "ingested" || Boolean(activeCandidateActionId)}
                    onClick={() => ignoreCandidate(candidate.id)}
                  >
                    {activeCandidateActionId === candidate.id ? "Updating..." : "Ignore"}
                  </button>
                </div>
              </div>
            ))
            : <div className="empty-state" style={{ display: "block" }}>{candidateEmptyMessage(candidateFilter, candidates.length)}</div>}
        </div>
      </article>
    </section>
  );
}

function interestRank(status: string) {
  const ranks: Record<string, number> = { interested: 0, neutral: 1, archived: 2 };
  return ranks[status] ?? 3;
}

function lastCheckChip(status: string) {
  const normalized = status.toLowerCase();
  if (!normalized) return { label: "Not checked", tone: "not-checked" };
  if (normalized.startsWith("ok:")) return { label: "OK", tone: "ok" };
  if (normalized.startsWith("error:")) return { label: "Error", tone: "error" };
  return { label: "Checked", tone: "checked" };
}

function lastCheckDetail(company: Company) {
  const status = company.last_check_status || "";
  if (!status) return "No scan run";
  const cleaned = status.replace(/^(ok|error):\s*/i, "");
  return company.last_checked_at ? cleaned : status;
}

function parseEvidence(source: CompanyCareerSource | null) {
  if (!source?.evidence) return [];
  try {
    const parsed = JSON.parse(source.evidence) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.map(item => String(item).trim()).filter(Boolean);
  } catch {
    return [];
  }
}
