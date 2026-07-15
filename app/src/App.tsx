import { useCallback, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { Link, NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { ActionsPage } from "./actions/ActionsPage";
import { buildAgentContext } from "./agent/agentContext";
import { HunterChat } from "./agent/HunterChat";
import { CandidatesPage } from "./candidates/CandidatesPage";
import { BriefcaseIcon, CalendarIcon, ChevronLeftIcon, ChevronRightIcon, GearIcon, HomeIcon, ListIcon, PeopleIcon, SearchIcon } from "./components/Icons";
import { CompaniesPage, CompanyDetailPage } from "./companies/CompaniesPage";
import { ContactsPage } from "./contacts/ContactsPage";
import type { AppState } from "./core/types";
import { routes } from "./core/routes";
import { DashboardPage } from "./dashboard/DashboardPage";
import { PostingDetailPage } from "./postings/PostingDetailPage";
import { PostingsPage } from "./postings/PostingsPage";
import { SettingsPage } from "./settings/SettingsPage";

type AppProps = {
  data: AppState;
  refresh: () => Promise<AppState>;
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
const DEFAULT_AGENT_PANEL_WIDTH = 400;

type AppShellStyle = CSSProperties & { "--agent-panel-width": string };

function storedBoolean(key: string): boolean {
  try {
    return window.localStorage.getItem(key) === "true";
  } catch {
    return false;
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

export function App({ data, refresh }: AppProps) {
  const closed = data.applications.filter(app => app.is_closed).length;
  const location = useLocation();
  const [agentOpen, setAgentOpen] = useState(false);
  const [agentPanelWidth, setAgentPanelWidth] = useState(storedAgentPanelWidth);
  const [navCollapsed, setNavCollapsed] = useState(() => storedBoolean(NAV_COLLAPSED_KEY));
  const shellRef = useRef<HTMLDivElement | null>(null);
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
        <Link className="sidebar-stat" to={routes.postingsFiltered({ stages: "posting-review" })}><span>Review</span><strong>{data.applications.filter(app => app.stage === "posting-review").length}</strong></Link>
        <Link className="sidebar-stat" to={routes.actionsFiltered({ status: "open" })}><span>Open actions</span><strong>{data.actions.filter(action => action.is_open).length}</strong></Link>
        <Link className="sidebar-stat" to={routes.postingsFiltered({ stages: "closed" })}><span>Closed</span><strong>{closed}</strong></Link>
      </aside>

      <main className="main">
        <AppNav mobile />

        <Routes>
          <Route path="/" element={<DashboardPage data={data} refresh={refresh} />} />
          <Route path="/postings" element={<PostingsPage data={data} />} />
          <Route path="/postings/new" element={<PostingDetailPage data={data} refresh={refresh} createNew />} />
          <Route path="/postings/:id" element={<PostingDetailPage data={data} refresh={refresh} />} />
          <Route path="/companies" element={<CompaniesPage data={data} refresh={refresh} />} />
          <Route path="/companies/new" element={<CompanyDetailPage data={data} refresh={refresh} createNew />} />
          <Route path="/companies/:id" element={<CompanyDetailPage data={data} refresh={refresh} />} />
          <Route path="/candidates" element={<CandidatesPage data={data} refresh={refresh} />} />
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
