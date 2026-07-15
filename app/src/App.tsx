import { Link, NavLink, Navigate, Route, Routes } from "react-router-dom";
import { ActionsPage } from "./actions/ActionsPage";
import { HunterChat } from "./agent/HunterChat";
import { CandidatesPage } from "./candidates/CandidatesPage";
import { BriefcaseIcon, CalendarIcon, GearIcon, HomeIcon, ListIcon, PeopleIcon, SearchIcon } from "./components/Icons";
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

function AppNav({ mobile = false }: { mobile?: boolean }) {
  const className = mobile ? "mobile-nav" : "nav-section";
  const links = navItems.map(item => (
    <NavLink key={item.to} to={item.to} end={item.end} className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>
      {item.icon}
      {item.label}
    </NavLink>
  ));

  if (mobile) return <nav className={className} aria-label="Dashboard sections">{links}</nav>;
  return <ul className={className}>{links.map((link, index) => <li key={navItems[index].to}>{link}</li>)}</ul>;
}

export function App({ data, refresh }: AppProps) {
  const closed = data.applications.filter(app => app.is_closed).length;

  return (
    <div className="app-shell">
      <aside className="sidebar" aria-label="Dashboard navigation">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true"><BriefcaseIcon size={18} /></span>
          <span>Hunter</span>
        </div>
        <AppNav />
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
      <HunterChat refresh={refresh} />
    </div>
  );
}
