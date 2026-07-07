export const routes = {
  dashboard: "/",
  postings: "/postings",
  postingDetail: (id: string) => `/postings/${encodeURIComponent(id)}`,
  companies: "/companies",
  companyNew: "/companies/new",
  companyDetail: (id: string) => `/companies/${encodeURIComponent(id)}`,
  candidates: "/candidates",
  actions: "/actions",
  contacts: "/contacts",
  settings: "/settings"
};
