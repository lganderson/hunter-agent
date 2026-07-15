import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";
import { DownloadIcon, ExternalIcon, FilterIcon, ListIcon, SearchIcon } from "../components/Icons";
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
import { dateOnlyLabel, titleCase } from "../core/format";
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

const INTEREST_STATUSES = ["interested", "neutral", "archived"];
const DEFAULT_INTEREST_STATUSES = ["interested", "neutral"];

export function CompaniesPage({ data, refresh }: CompaniesPageProps) {
  const [search, setSearch] = useState("");
  const [interestStatuses, setInterestStatuses] = useState<string[]>(DEFAULT_INTEREST_STATUSES);
  const [checkingCompanyId, setCheckingCompanyId] = useState("");
  const [operationStatus, setOperationStatus] = useState("");

  const rows = useMemo(() => {
    const query = search.toLowerCase();
    return data.companies
      .filter(company => {
        if (!interestStatuses.includes(company.interest_status)) return false;
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
      .sort((a, b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id));
  }, [data.companies, interestStatuses, search]);

  function clearFilters() {
    setSearch("");
    setInterestStatuses(DEFAULT_INTEREST_STATUSES);
  }

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
          <MultiFilter label="Interest" values={INTEREST_STATUSES} selected={interestStatuses} onChange={setInterestStatuses} />
          <button className="button" type="button" onClick={clearFilters}><FilterIcon size={16} /> Clear</button>
          <a className="button icon-button" href="/api/companies/export" aria-label="Export company data" title="Export company data"><DownloadIcon /></a>
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
    if (!company || isCheckingCareers) return;
    setIsCheckingCareers(true);
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
    } finally {
      setIsCheckingCareers(false);
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
              <span className={`company-interest ${company.interest_status || "neutral"}`}>{titleCase(company.interest_status || "neutral")}</span>
            </div>
            <p>{company.aliases ? `Also known as ${company.aliases}` : `Company record ${company.id}`}</p>
          </div>
        </div>
        <div className="company-primary-actions">
          {company.website ? <a className="button" href={company.website} target="_blank" rel="noreferrer"><ExternalIcon size={16} /> Website</a> : null}
          {company.careers_url ? <a className="button" href={company.careers_url} target="_blank" rel="noreferrer"><ExternalIcon size={16} /> Careers</a> : null}
          <button className="button primary" type="button" disabled={!company.careers_url || isCheckingCareers} onClick={checkCareers}>
            <SearchIcon size={16} /> {isCheckingCareers ? "Checking…" : "Check careers"}
          </button>
        </div>
      </header>

      <div className="company-overview-grid" aria-label="Company overview">
        <Link className="company-stat" to={routes.candidatesFiltered({ companies: company.id, status: "recommended" })} aria-label={`View recommended role candidates for ${company.name}`}>
          <span>Recommended roles</span><strong>{recommendedCount}</strong><small>{candidateCounts.new} new from latest check</small>
        </Link>
        <Link className="company-stat" to={routes.postingsFiltered({ companies: company.name })} aria-label={`View tracked postings for ${company.name}`}>
          <span>Tracked postings</span><strong>{linkedPostings.length}</strong><small>{linkedPostings.filter(app => app.is_active).length} active</small>
        </Link>
        <Link className="company-stat" to={routes.contactsFiltered({ company_id: company.id })} aria-label={`View contacts linked to ${company.name}`}>
          <span>Contacts</span><strong>{linkedContacts.length}</strong><small>{linkedContacts.length ? "Linked to this company" : "No relationships yet"}</small>
        </Link>
        <Link className="company-stat" to={routes.candidatesFiltered({ companies: company.id, status: "all", latest: "true" })} aria-label={`View candidates from the latest ${company.name} careers check`}>
          <span>Last careers check</span><strong className="company-stat-date">{lastCheckDetail(company)}</strong><small>{lastCheckChip(company.last_check_status).label}</small>
        </Link>
      </div>

      {operationStatus ? <div className="company-operation-status" role="status" aria-live="polite">{operationStatus}</div> : null}

      <div className="company-workspace">
        <div className="company-workspace-main">
          <article className="panel company-candidates-panel">
            <div className="company-section-header">
              <div>
                <h2>Role candidates</h2>
                <p>Review roles found on this company’s careers source.</p>
              </div>
              <span>{candidates.length} total</span>
            </div>
            <div className="candidate-filter-bar" aria-label="Candidate filters">
              {CANDIDATE_FILTERS.map(filter => (
                <button
                  className={candidateFilter === filter.id ? "candidate-filter active" : "candidate-filter"}
                  key={filter.id}
                  type="button"
                  onClick={() => setCandidateFilter(filter.id)}
                  aria-pressed={candidateFilter === filter.id}
                >
                  {filter.label}
                  <span>{candidateCounts[filter.id]}</span>
                </button>
              ))}
            </div>
            <div className="company-candidate-list">
          {visibleCandidates.length
            ? visibleCandidates.map(candidate => (
              <article className="company-candidate" key={candidate.id}>
                <div className="company-candidate-copy">
                  <a className="company-candidate-title" href={candidate.url} target="_blank" rel="noreferrer">{candidate.title || candidate.url}</a>
                  <div className="candidate-meta">
                    <span>{candidate.location || "Location not listed"}</span>
                    <span>{titleCase(candidate.status)} · Seen {candidateDateLabel(candidate)}</span>
                    {candidate.fit_score ? <span className={`pill fit-${fitBand(candidate)}`}>Fit {candidate.fit_score}</span> : null}
                  </div>
                  {candidate.fit_summary ? <span className="candidate-fit-summary">{candidate.fit_summary}</span> : null}
                </div>
                <div className="company-candidate-actions">
                  <a className="button compact" href={candidate.url} target="_blank" rel="noreferrer">View role</a>
                  <button
                    className="button compact primary"
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
              </article>
            ))
            : <div className="company-section-empty">{candidateEmptyMessage(candidateFilter, candidates.length)}</div>}
            </div>
          </article>

          <div className="company-related-grid">
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

            <article className="panel company-rail-panel">
              <div className="company-section-header compact"><div><h2>Tracked postings</h2><p>{linkedPostings.length} posting{linkedPostings.length === 1 ? "" : "s"} tied to this company.</p></div></div>
              <div className="company-relationship-list">
                {linkedPostings.length ? linkedPostings.map(app => (
                  <Link className="company-relationship linked" key={app.id} to={routes.postingDetail(app.id)}>
                    <div><strong>{app.role || app.id}</strong><span>{titleCase(app.stage)}{app.outcome ? ` · ${titleCase(app.outcome)}` : ""}</span></div>
                    <span>Open</span>
                  </Link>
                )) : <div className="company-section-empty compact">No tracked postings yet.</div>}
              </div>
            </article>
          </div>
        </div>

        <aside className="company-workspace-rail">
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
            {careerSource ? (
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
        <option value="archived">Archived</option>
      </select></label>
      <label className="form-field">Aliases <input name="aliases" type="text" defaultValue={company?.aliases || ""} /></label>
      <label className="form-field">Website <input name="website" type="url" defaultValue={company?.website || ""} /></label>
      <label className="form-field">Careers URL <input name="careers_url" type="url" defaultValue={company?.careers_url || ""} /></label>
      <label className="form-field full">Notes <textarea name="notes" defaultValue={company?.notes || ""} /></label>
      <div className="form-field full"><button className="button primary" type="submit"><FilterIcon size={16} /> Save company</button></div>
    </form>
  );
}

function candidateDateLabel(candidate: CompanyPostingCandidate) {
  const value = candidate.last_seen_at || candidate.first_seen_at;
  return value ? dateOnlyLabel(value) : "unknown";
}

function lastCheckChip(status: string) {
  const normalized = status.toLowerCase();
  if (!normalized) return { label: "Not checked", tone: "not-checked" };
  if (normalized.startsWith("ok:")) return { label: "OK", tone: "ok" };
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
