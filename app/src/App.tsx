import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { CSSProperties } from "react";
import { Link, NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { ActionsPage } from "./actions/ActionsPage";
import { buildAgentContext } from "./agent/agentContext";
import { HunterChat } from "./agent/HunterChat";
import { CandidatesPage } from "./candidates/CandidatesPage";
import { BriefcaseIcon, CalendarIcon, ChevronLeftIcon, ChevronRightIcon, GearIcon, HomeIcon, ListIcon, PeopleIcon, SearchIcon } from "./components/Icons";
import { CompaniesPage, CompanyDetailPage } from "./companies/CompaniesPage";
import { ContactsPage } from "./contacts/ContactsPage";
import type { AppState, Application, CompanyPostingCandidate, DiscoveryCandidate } from "./core/types";
import { routes } from "./core/routes";
import { DashboardPage } from "./dashboard/DashboardPage";
import { PostingDetailPage } from "./postings/PostingDetailPage";
import { PostingsPage } from "./postings/PostingsPage";
import { SettingsPage } from "./settings/SettingsPage";
import { getCandidateEnrichmentJob, getCompanyDiscoveryJob, startCandidateDiscovery, startCandidateEnrichment, startCompanyDiscovery, startCompanyEvaluation } from "./core/api";
import type { CandidateEnrichmentJob, CompanyDiscoveryJob } from "./core/types";
import type { ActionUpdateResult } from "./core/useHunterData";
import { readModelQueryKeys } from "./core/queryKeys";

type AppProps = {
  data: AppState;
  refresh: () => Promise<AppState>;
  applyActionUpdate: (result: ActionUpdateResult) => void;
  applyApplicationUpdate: (application: AppState["applications"][number]) => void;
  applyCompanyCandidateUpdates: (candidates: CompanyPostingCandidate[]) => void;
  applyDiscoveryCandidateUpdate: (candidate: DiscoveryCandidate, posting?: Application | null, removePostingId?: string) => void;
};

const navItems = [
  { to: "/", label: "Dashboard", icon: <HomeIcon />, end: true },
  { to: "/postings", label: "Postings", icon: <ListIcon /> },
  { to: "/companies", label: "Companies", icon: <BriefcaseIcon /> },
  { to: "/candidates", label: "Candidates", icon: <SearchIcon /> },
  { to: "/actions", label: "Actions", icon: <CalendarIcon /> },
  { to: "/contacts", label: "Contacts", icon: <PeopleIcon /> },
  { to: "/settings", label: "Settings", icon: <GearIcon /> }
];

const AGENT_PANEL_WIDTH_KEY = "hunter-agent-panel-width-v1";
const NAV_COLLAPSED_KEY = "hunter-nav-collapsed-v1";
const DISMISSED_COMPANY_DISCOVERY_JOB_KEY = "hunter-dismissed-company-discovery-job-v1";
const DISMISSED_CANDIDATE_ENRICHMENT_JOB_KEY = "hunter-dismissed-candidate-enrichment-job-v1";
const DEFAULT_AGENT_PANEL_WIDTH = 400;

type AppShellStyle = CSSProperties & { "--agent-panel-width": string };

function storedBoolean(key: string): boolean {
  try {
    return window.localStorage.getItem(key) === "true";
  } catch {
    return false;
  }
}

function storedString(key: string): string {
  try {
    return window.localStorage.getItem(key) || "";
  } catch {
    return "";
  }
}

function storedAgentPanelWidth(): number {
  try {
    const stored = Number.parseInt(window.localStorage.getItem(AGENT_PANEL_WIDTH_KEY) || "", 10);
    if (!Number.isFinite(stored)) return DEFAULT_AGENT_PANEL_WIDTH;
    const viewportMaximum = Math.max(320, Math.min(720, window.innerWidth - 640));
    return Math.max(320, Math.min(viewportMaximum, stored));
  } catch {
    return DEFAULT_AGENT_PANEL_WIDTH;
  }
}

function AppNav({ collapsed = false, mobile = false }: { collapsed?: boolean; mobile?: boolean }) {
  const className = mobile ? "mobile-nav" : "nav-section";
  const links = navItems.map(item => (
    <NavLink
      key={item.to}
      to={item.to}
      end={item.end}
      className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}
      aria-label={item.label}
      title={collapsed ? item.label : undefined}
    >
      {item.icon}
      <span className="nav-item-label">{item.label}</span>
    </NavLink>
  ));

  if (mobile) return <nav className={className} aria-label="Dashboard sections">{links}</nav>;
  return <ul className={className}>{links.map((link, index) => <li key={navItems[index].to}>{link}</li>)}</ul>;
}

export function App({ data, refresh, applyActionUpdate, applyApplicationUpdate, applyCompanyCandidateUpdates, applyDiscoveryCandidateUpdate }: AppProps) {
  const queryClient = useQueryClient();
  const closed = data.applications.filter(app => app.is_closed).length;
  const location = useLocation();
  const [agentOpen, setAgentOpen] = useState(false);
  const [agentPanelWidth, setAgentPanelWidth] = useState(storedAgentPanelWidth);
  const [navCollapsed, setNavCollapsed] = useState(() => storedBoolean(NAV_COLLAPSED_KEY));
  const shellRef = useRef<HTMLDivElement | null>(null);
  const [companyDiscoveryJob, setCompanyDiscoveryJob] = useState<CompanyDiscoveryJob | null>(null);
  const [candidateEnrichmentJob, setCandidateEnrichmentJob] = useState<CandidateEnrichmentJob | null>(null);
  const lastRefreshedDiscoveryJob = useRef("");
  const lastRefreshedEnrichmentJob = useRef("");
  const agentContext = useMemo(
    () => buildAgentContext(location.pathname, location.search, data),
    [data, location.pathname, location.search]
  );

  const resizeAgentPanel = useCallback((width: number, commit: boolean) => {
    shellRef.current?.style.setProperty("--agent-panel-width", `${width}px`);
    if (!commit) return;
    setAgentPanelWidth(width);
    try {
      window.localStorage.setItem(AGENT_PANEL_WIDTH_KEY, String(width));
    } catch {
      // The panel still resizes for this session when local storage is unavailable.
    }
  }, []);

  useEffect(() => {
    let active = true;
    async function pollDiscoveryJob() {
      try {
        const response = await getCompanyDiscoveryJob();
        if (!active) return;
        setCompanyDiscoveryJob(response.job);
        if (
          response.job?.status === "completed"
          && lastRefreshedDiscoveryJob.current !== response.job.id
        ) {
          lastRefreshedDiscoveryJob.current = response.job.id;
          await refresh();
        }
      } catch {
        // The rest of Hunter remains usable while the local job endpoint reconnects.
      }
    }
    void pollDiscoveryJob();
    const interval = window.setInterval(() => void pollDiscoveryJob(), 1_000);
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, [refresh]);

  useEffect(() => {
    let active = true;
    async function pollEnrichmentJob() {
      try {
        const response = await getCandidateEnrichmentJob();
        if (!active) return;
        setCandidateEnrichmentJob(response.job);
        if (
          response.job?.status === "completed"
          && lastRefreshedEnrichmentJob.current !== response.job.id
        ) {
          lastRefreshedEnrichmentJob.current = response.job.id;
          await Promise.all([
            refresh(),
            queryClient.invalidateQueries({ queryKey: readModelQueryKeys.candidateLists("discovery") })
          ]);
          await queryClient.invalidateQueries({
            queryKey: readModelQueryKeys.candidateDetails("discovery"),
            refetchType: "none"
          });
        }
      } catch {
        // The rest of Hunter remains usable while the local job endpoint reconnects.
      }
    }
    void pollEnrichmentJob();
    const interval = window.setInterval(() => void pollEnrichmentJob(), 1_000);
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, [queryClient, refresh]);

  const beginCompanyDiscovery = useCallback(async (payload: CompanyDiscoveryJob["request"]) => {
    const response = await startCompanyDiscovery({
      focus: payload.focus || "",
      sizes: payload.sizes || [],
      sources: payload.sources || [],
      locations: payload.locations || [],
      remote_region: payload.remote_region || "",
      metro_area: payload.metro_area || ""
    });
    setCompanyDiscoveryJob(response.job);
    return response.job;
  }, []);

  const beginCompanyEvaluation = useCallback(async (payload: CompanyDiscoveryJob["request"]) => {
    const response = await startCompanyEvaluation({
      focus: payload.focus || "",
      sizes: payload.sizes || [],
      locations: payload.locations || [],
      remote_region: payload.remote_region || "",
      metro_area: payload.metro_area || "",
      tracking_status: payload.tracking_status || "discovered",
      force: payload.force,
      reason: payload.reason
    });
    setCompanyDiscoveryJob(response.job);
    return response.job;
  }, []);

  const beginCandidateEnrichment = useCallback(async (payload: CandidateEnrichmentJob["request"]) => {
    const response = await startCandidateEnrichment(payload);
    setCandidateEnrichmentJob(response.job);
    return response.job;
  }, []);

  const beginCandidateDiscovery = useCallback(async (payload: CandidateEnrichmentJob["request"]) => {
    const response = await startCandidateDiscovery({
      search_id: payload.search_id || "",
      enrichment_limit: payload.enrichment_limit
    });
    setCandidateEnrichmentJob(response.job);
    return response.job;
  }, []);

  function toggleNavigation() {
    setNavCollapsed(current => {
      const next = !current;
      try {
        window.localStorage.setItem(NAV_COLLAPSED_KEY, String(next));
      } catch {
        // Collapsing still works for this session when local storage is unavailable.
      }
      return next;
    });
  }

  const shellStyle: AppShellStyle = { "--agent-panel-width": `${agentPanelWidth}px` };

  return (
    <div ref={shellRef} className={`app-shell${agentOpen ? " agent-open" : ""}${navCollapsed ? " nav-collapsed" : ""}`} style={shellStyle}>
      <aside className={`sidebar${navCollapsed ? " collapsed" : ""}`} aria-label="Dashboard navigation">
        <div className="brand">
          <div className="brand-identity">
            <span className="brand-mark" aria-hidden="true"><BriefcaseIcon size={18} /></span>
            <span className="brand-name">Hunter</span>
          </div>
          <button
            className="sidebar-toggle"
            type="button"
            onClick={toggleNavigation}
            aria-label={navCollapsed ? "Expand navigation" : "Collapse navigation"}
            aria-expanded={!navCollapsed}
            title={navCollapsed ? "Expand navigation" : "Collapse navigation"}
          >
            {navCollapsed ? <ChevronRightIcon size={16} /> : <ChevronLeftIcon size={16} />}
          </button>
        </div>
        <AppNav collapsed={navCollapsed} />
        <div className="sidebar-label">Views</div>
        <Link className="sidebar-stat" to={routes.postingsFiltered({ stages: "considering" })}><span>Considering</span><strong>{data.applications.filter(app => app.stage === "considering").length}</strong></Link>
        <Link className="sidebar-stat" to={routes.actionsFiltered({ status: "open" })}><span>Open actions</span><strong>{data.actions.filter(action => action.is_open).length}</strong></Link>
        <Link className="sidebar-stat" to={routes.postingsFiltered({ stages: "closed" })}><span>Closed</span><strong>{closed}</strong></Link>
      </aside>

      <main className="main">
        <AppNav mobile />

        <CompanyDiscoveryJobBanner job={companyDiscoveryJob} />
        <CandidateEnrichmentJobBanner job={candidateEnrichmentJob} />

        <Routes>
          <Route path="/" element={<DashboardPage data={data} refresh={refresh} />} />
          <Route path="/postings" element={<PostingsPage data={data} />} />
          <Route path="/postings/new" element={<PostingDetailPage data={data} refresh={refresh} createNew />} />
          <Route path="/postings/:id" element={<PostingDetailPage data={data} refresh={refresh} applyActionUpdate={applyActionUpdate} applyApplicationUpdate={applyApplicationUpdate} />} />
          <Route path="/companies" element={<CompaniesPage data={data} refresh={refresh} discoveryJob={companyDiscoveryJob} startDiscoveryJob={beginCompanyDiscovery} startEvaluationJob={beginCompanyEvaluation} />} />
          <Route path="/companies/new" element={<CompanyDetailPage data={data} refresh={refresh} applyCompanyCandidateUpdates={applyCompanyCandidateUpdates} createNew />} />
          <Route path="/companies/:id" element={<CompanyDetailPage data={data} refresh={refresh} applyCompanyCandidateUpdates={applyCompanyCandidateUpdates} />} />
          <Route path="/candidates" element={<CandidatesPage data={data} refresh={refresh} applyCompanyCandidateUpdates={applyCompanyCandidateUpdates} applyDiscoveryCandidateUpdate={applyDiscoveryCandidateUpdate} enrichmentJob={candidateEnrichmentJob} startDiscoveryJob={beginCandidateDiscovery} startEnrichmentJob={beginCandidateEnrichment} />} />
          <Route path="/actions" element={<ActionsPage data={data} refresh={refresh} />} />
          <Route path="/contacts" element={<ContactsPage data={data} refresh={refresh} />} />
          <Route path="/settings" element={<SettingsPage refresh={refresh} />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
      <HunterChat
        context={agentContext}
        data={data}
        onOpenChange={setAgentOpen}
        onPanelWidthChange={resizeAgentPanel}
        open={agentOpen}
        panelWidth={agentPanelWidth}
        refresh={refresh}
      />
    </div>
  );
}

function CandidateEnrichmentJobBanner({ job }: { job: CandidateEnrichmentJob | null }) {
  const [dismissedJobId, setDismissedJobId] = useState(() => storedString(DISMISSED_CANDIDATE_ENRICHMENT_JOB_KEY));
  if (!job) return null;
  const active = job.status === "queued" || job.status === "running";
  const discovering = job.job_type === "candidate-discovery";
  if (job.status === "completed" && discovering) return null;
  if (!active && dismissedJobId === job.id) return null;
  const jobId = job.id;
  const maximum = Math.max(1, job.total_steps || 1);
  const value = Math.min(maximum, Math.max(0, job.completed_steps || 0));

  function dismissJob() {
    setDismissedJobId(jobId);
    try {
      window.localStorage.setItem(DISMISSED_CANDIDATE_ENRICHMENT_JOB_KEY, jobId);
    } catch {
      // The banner still dismisses for this session when local storage is unavailable.
    }
  }

  return (
    <aside className={`global-job-status ${job.status}`} aria-live="polite" aria-label={discovering ? "Candidate Discovery status" : "Candidate enrichment status"}>
      <div className="global-job-status-copy">
        <strong>{job.status === "completed"
          ? "Existing role refresh complete"
          : active
            ? discovering ? "Hunter is finding new roles" : "Hunter is refreshing existing roles"
            : discovering ? "Finding new roles needs attention" : "Refreshing existing roles needs attention"}</strong>
        <span>{job.message}</span>
      </div>
      <div className="global-job-status-progress">
        <progress value={value} max={maximum} aria-label="Candidate enrichment progress" />
        <span>{Math.round((value / maximum) * 100)}%</span>
      </div>
      <Link className="button compact" to="/candidates?mode=discovery&discovery_status=needs-decision">View roles</Link>
      {!active ? <button className="button compact secondary" type="button" onClick={dismissJob}>Dismiss</button> : null}
    </aside>
  );
}

function CompanyDiscoveryJobBanner({ job }: { job: CompanyDiscoveryJob | null }) {
  const [dismissedJobId, setDismissedJobId] = useState(() => storedString(DISMISSED_COMPANY_DISCOVERY_JOB_KEY));
  if (!job) return null;
  const active = job.status === "queued" || job.status === "running";
  if (job.status === "completed") return null;
  if (!active && dismissedJobId === job.id) return null;
  const jobId = job.id;
  const evaluating = job.job_type === "company-evaluation";
  const maximum = Math.max(1, job.total_steps || 1);
  const value = Math.min(maximum, Math.max(0, job.completed_steps || 0));

  function dismissJob() {
    setDismissedJobId(jobId);
    try {
      window.localStorage.setItem(DISMISSED_COMPANY_DISCOVERY_JOB_KEY, jobId);
    } catch {
      // The banner still dismisses for this session when local storage is unavailable.
    }
  }

  return (
    <aside className={`global-job-status ${job.status}`} aria-live="polite" aria-label={evaluating ? "Company evaluation status" : "Company discovery status"}>
      <div className="global-job-status-copy">
        <strong>{active
          ? evaluating ? "Company evaluation running" : "Company discovery running"
          : evaluating ? "Company evaluation needs attention" : "Company discovery needs attention"}</strong>
        <span>{job.message}</span>
      </div>
      <div className="global-job-status-progress">
        <progress value={value} max={maximum} aria-label={evaluating ? "Company evaluation progress" : "Company discovery progress"} />
        <span>{Math.round((value / maximum) * 100)}%</span>
      </div>
      <Link className="button compact" to="/companies">View companies</Link>
      {!active ? <button className="button compact secondary" type="button" onClick={dismissJob}>Dismiss</button> : null}
    </aside>
  );
}
