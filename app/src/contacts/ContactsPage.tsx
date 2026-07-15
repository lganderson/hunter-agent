import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { FilterIcon, ListIcon, PeopleIcon, SearchIcon, XIcon } from "../components/Icons";
import { linkCompanyContact, linkContact, unlinkCompanyContact, unlinkContact, upsertContact } from "../core/api";
import { titleCase } from "../core/format";
import type { AppState, Contact } from "../core/types";

type ContactsPageProps = {
  data: AppState;
  refresh: () => Promise<AppState>;
};

export function ContactsPage({ data, refresh }: ContactsPageProps) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [search, setSearch] = useState("");
  const [selectedContactId, setSelectedContactId] = useState("");
  const [operationStatus, setOperationStatus] = useState("");
  const selectedContact = data.contacts.find(contact => contact.id === selectedContactId) || null;
  const companyId = searchParams.get("company_id") || "";
  const filteredCompany = data.companies.find(company => company.id === companyId) || null;
  const companyContactIds = useMemo(
    () => new Set(data.company_contacts.filter(link => link.company_id === companyId).map(link => link.contact_id)),
    [companyId, data.company_contacts]
  );
  const rows = data.contacts
    .filter(contact => {
      if (companyId && !companyContactIds.has(contact.id)) return false;
      const query = search.toLowerCase();
      if (!query) return true;
      return [
        contact.id,
        contact.name,
        contact.company,
        contact.role,
        contact.email,
        contact.linkedin,
        contact.relationship,
        contact.status,
        contact.notes
      ].join(" ").toLowerCase().includes(query);
    })
    .sort((a, b) => (a.company || "").localeCompare(b.company || "") || (a.name || "").localeCompare(b.name || ""));

  function closeModal() {
    setSelectedContactId("");
    setOperationStatus("");
  }

  return (
    <section className="view-section" id="contacts-view" aria-label="Contacts">
      <div className="contact-layout">
        <article className="panel">
          <div className="toolbar" aria-label="Contact tools">
            <label className="search">
              <span className="sr-only">Search contacts</span>
              <SearchIcon />
              <input value={search} onChange={event => setSearch(event.target.value)} type="search" placeholder="Search contacts, companies, notes..." />
            </label>
            {companyId ? <button className="button" type="button" onClick={() => setSearchParams({})}><FilterIcon size={16} /> Clear company</button> : null}
            <button className="button primary" type="button" onClick={() => setSelectedContactId("new")}><PeopleIcon /> New Contact</button>
            {companyId ? <span className="active-filter">Company: {filteredCompany?.name || companyId}</span> : null}
          </div>
          <div className="table-scroll">
            <table className="simple-table">
              <thead>
                <tr>
                  <th>Contact</th>
                  <th>Status</th>
                  <th>Relationship</th>
                  <th>Next follow-up</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(contact => (
                  <tr key={contact.id} data-contact-id={contact.id}>
                    <td className="role-cell"><button className="row-select" type="button" onClick={() => setSelectedContactId(contact.id)}><strong>{contact.name || "Unnamed contact"}</strong><span>{[contact.company, contact.role].filter(Boolean).join(" · ") || "No company or role"}</span></button></td>
                    <td>{titleCase(contact.status)}</td>
                    <td>{contact.relationship || "Not listed"}</td>
                    <td>{contact.next_follow_up || "None"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="empty-state" style={{ display: rows.length ? "none" : "block" }}>No contacts match the current filters.</div>
          </div>
        </article>
      </div>

      {selectedContactId && (
        <ContactModal
          contact={selectedContact}
          data={data}
          operationStatus={operationStatus}
          setOperationStatus={setOperationStatus}
          closeModal={closeModal}
          refresh={refresh}
          setSelectedContactId={setSelectedContactId}
        />
      )}
    </section>
  );
}

function ContactModal({
  contact,
  data,
  operationStatus,
  setOperationStatus,
  closeModal,
  refresh,
  setSelectedContactId
}: {
  contact: Contact | null;
  data: AppState;
  operationStatus: string;
  setOperationStatus: (status: string) => void;
  closeModal: () => void;
  refresh: () => Promise<AppState>;
  setSelectedContactId: (id: string) => void;
}) {
  const linkedApps = useMemo(() => {
    if (!contact) return [];
    const linkedIds = new Set(data.application_contacts.filter(link => link.contact_id === contact.id).map(link => link.application_id));
    return data.applications.filter(app => linkedIds.has(app.id));
  }, [contact, data]);
  const linkedCompanies = useMemo(() => {
    if (!contact) return [];
    const linkedIds = new Set(data.company_contacts.filter(link => link.contact_id === contact.id).map(link => link.company_id));
    return data.companies.filter(company => linkedIds.has(company.id));
  }, [contact, data]);
  const linkedCompanyIds = useMemo(() => new Set(linkedCompanies.map(company => company.id)), [linkedCompanies]);
  const availableCompanies = useMemo(() => data.companies.filter(company => !linkedCompanyIds.has(company.id)), [data.companies, linkedCompanyIds]);
  const [postingId, setPostingId] = useState(data.applications[0]?.id || "");
  const [companyId, setCompanyId] = useState(availableCompanies[0]?.id || "");

  useEffect(() => {
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = "";
    };
  }, []);

  useEffect(() => {
    setCompanyId(availableCompanies[0]?.id || "");
  }, [availableCompanies]);

  async function saveContact(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setOperationStatus("Saving contact...");
    try {
      const result = await upsertContact(contact?.id || "", {
        name: String(form.get("name") || ""),
        company: String(form.get("company") || ""),
        role: String(form.get("role") || ""),
        email: String(form.get("email") || ""),
        linkedin: String(form.get("linkedin") || ""),
        relationship: String(form.get("relationship") || ""),
        status: String(form.get("status") || ""),
        last_contacted: String(form.get("last_contacted") || ""),
        next_follow_up: String(form.get("next_follow_up") || ""),
        notes: String(form.get("notes") || "")
      });
      await refresh();
      setSelectedContactId(result.contact.id);
      setOperationStatus("Contact saved.");
    } catch (error) {
      setOperationStatus(`Could not save contact. Run make serve-app. ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  async function linkPosting() {
    if (!contact || !postingId) return;
    setOperationStatus("Linking posting...");
    try {
      await linkContact(contact.id, postingId);
      await refresh();
      setOperationStatus("Posting linked.");
    } catch (error) {
      setOperationStatus(`Could not link posting. ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  async function removeAssociation(applicationId: string) {
    if (!contact) return;
    setOperationStatus("Removing association...");
    try {
      await unlinkContact(contact.id, applicationId);
      await refresh();
      setOperationStatus("Association removed.");
    } catch (error) {
      setOperationStatus(`Could not remove association. ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  async function linkCompany() {
    if (!contact || !companyId) return;
    setOperationStatus("Linking company...");
    try {
      await linkCompanyContact(companyId, contact.id);
      await refresh();
      setOperationStatus("Company linked.");
    } catch (error) {
      setOperationStatus(`Could not link company. ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  async function removeCompanyAssociation(companyIdToRemove: string) {
    if (!contact) return;
    setOperationStatus("Removing company association...");
    try {
      await unlinkCompanyContact(companyIdToRemove, contact.id);
      await refresh();
      setOperationStatus("Company association removed.");
    } catch (error) {
      setOperationStatus(`Could not remove company association. ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  return (
    <div className="modal-backdrop">
      <article className="modal" role="dialog" aria-modal="true" aria-labelledby="contact-form-title">
        <div className="modal-header">
          <h2 id="contact-form-title">{contact ? contact.name || "Unnamed contact" : "New Contact"}</h2>
          <button className="button compact" type="button" onClick={closeModal}><XIcon size={18} /> Close</button>
        </div>
        <form className="management-form" onSubmit={saveContact}>
          <label className="form-field full">Name <input name="name" type="text" required defaultValue={contact?.name || ""} autoFocus /></label>
          <label className="form-field">Company <input name="company" type="text" defaultValue={contact?.company || ""} /></label>
          <label className="form-field">Role <input name="role" type="text" defaultValue={contact?.role || ""} /></label>
          <label className="form-field">Email <input name="email" type="email" defaultValue={contact?.email || ""} /></label>
          <label className="form-field">LinkedIn <input name="linkedin" type="url" defaultValue={contact?.linkedin || ""} /></label>
          <label className="form-field">Relationship <input name="relationship" type="text" defaultValue={contact?.relationship || ""} /></label>
          <label className="form-field">Status <input name="status" type="text" defaultValue={contact?.status || ""} /></label>
          <label className="form-field">Last contacted <input name="last_contacted" type="date" defaultValue={contact?.last_contacted || ""} /></label>
          <label className="form-field">Next follow-up <input name="next_follow_up" type="date" defaultValue={contact?.next_follow_up || ""} /></label>
          <label className="form-field full">Notes <textarea name="notes" defaultValue={contact?.notes || ""} /></label>
          <div className="detail-actions form-field full">
            <button className="button primary" type="submit"><FilterIcon size={16} /> Save Contact</button>
            <button className="button" type="button" onClick={closeModal}>Cancel</button>
          </div>
        </form>
        <div className="detail-status">{operationStatus}</div>

        <div className="panel-header"><h2 className="panel-title">Associated Postings</h2></div>
        <div className="management-form">
          <label className="form-field full">Posting <select value={postingId} onChange={event => setPostingId(event.target.value)}>
            {data.applications.slice().sort((a, b) => (a.company || "").localeCompare(b.company || "") || (a.role || "").localeCompare(b.role || "")).map(app => (
              <option key={app.id} value={app.id}>{app.company || "Unknown"} · {app.role || app.id}</option>
            ))}
          </select></label>
          <div className="detail-actions form-field full">
            <button className="button" type="button" disabled={!contact} onClick={linkPosting}><ListIcon /> Link Posting</button>
          </div>
        </div>
        <div className="association-list">
          {contact
            ? linkedApps.length
              ? linkedApps.map(app => (
                <div className="association-row" key={app.id}>
                  <div>
                    <strong>{app.role || app.id}</strong>
                    <span>{app.company || "Unknown company"} · {titleCase(app.stage)}{app.outcome ? ` · ${titleCase(app.outcome)}` : ""}</span>
                  </div>
                  <button className="button compact" type="button" onClick={() => removeAssociation(app.id)}>Unlink</button>
                </div>
              ))
              : <div className="empty-state" style={{ display: "block" }}>No postings are associated with this contact.</div>
            : <div className="empty-state" style={{ display: "block" }}>Save the contact before linking postings.</div>}
        </div>

        <div className="panel-header"><h2 className="panel-title">Associated Companies</h2></div>
        <div className="management-form">
          <label className="form-field full">Company <select value={companyId} onChange={event => setCompanyId(event.target.value)} disabled={!availableCompanies.length}>
            {availableCompanies.map(company => (
              <option key={company.id} value={company.id}>{company.name}</option>
            ))}
          </select></label>
          <div className="detail-actions form-field full">
            <button className="button" type="button" disabled={!contact || !companyId} onClick={linkCompany}><ListIcon /> Link Company</button>
          </div>
        </div>
        <div className="association-list">
          {contact
            ? linkedCompanies.length
              ? linkedCompanies.map(company => (
                <div className="association-row" key={company.id}>
                  <div>
                    <strong>{company.name}</strong>
                    <span>{titleCase(company.interest_status)}{company.careers_url ? ` · ${company.careers_url}` : ""}</span>
                  </div>
                  <button className="button compact" type="button" onClick={() => removeCompanyAssociation(company.id)}>Unlink</button>
                </div>
              ))
              : <div className="empty-state" style={{ display: "block" }}>No companies are associated with this contact.</div>
            : <div className="empty-state" style={{ display: "block" }}>Save the contact before linking companies.</div>}
        </div>
      </article>
    </div>
  );
}
