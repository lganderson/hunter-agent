export const routes = {
  dashboard: "/",
  postings: "/postings",
  postingsFiltered: (filters: Record<string, string>) => withQuery("/postings", filters),
  postingDetail: (id: string) => `/postings/${encodeURIComponent(id)}`,
  companies: "/companies",
  companyNew: "/companies/new",
  companyDetail: (id: string) => `/companies/${encodeURIComponent(id)}`,
  candidates: "/candidates",
  actions: "/actions",
  actionsFiltered: (filters: Record<string, string>) => withQuery("/actions", filters),
  contacts: "/contacts",
  settings: "/settings"
};

function withQuery(path: string, values: Record<string, string>): string {
  const query = new URLSearchParams(values);
  return `${path}?${query.toString()}`;
}
