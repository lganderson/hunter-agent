"""Active Hunter data repository.

CSV remains an import/export format. Once the SQLite database is initialized,
runtime reads and writes use SQLite as the local app store.
"""

from . import paths, schema, sqlite_store, storage


def using_sqlite():
    return sqlite_store.is_initialized()


def backend_name():
    return "sqlite" if using_sqlite() else "csv"


def read_applications():
    if using_sqlite():
        return sqlite_store.read_applications()
    return storage.read_rows(paths.APPLICATIONS, schema.APPLICATION_FIELDS)


def write_applications(rows):
    if using_sqlite():
        sqlite_store.write_applications(rows)
        return
    storage.write_rows(paths.APPLICATIONS, schema.APPLICATION_FIELDS, rows)


def read_actions():
    if using_sqlite():
        return sqlite_store.read_actions()
    return storage.read_rows(paths.ACTIONS, schema.ACTION_FIELDS)


def write_actions(rows):
    if using_sqlite():
        sqlite_store.write_actions(rows)
        return
    storage.write_rows(paths.ACTIONS, schema.ACTION_FIELDS, rows)


def read_contacts():
    if using_sqlite():
        return sqlite_store.read_contacts()
    return storage.read_rows(paths.CONTACTS, schema.CONTACT_FIELDS)


def write_contacts(rows):
    if using_sqlite():
        sqlite_store.write_contacts(rows)
        return
    storage.write_rows(paths.CONTACTS, schema.CONTACT_FIELDS, rows)


def read_companies():
    if using_sqlite():
        return sqlite_store.read_companies()
    return []


def write_companies(rows):
    if using_sqlite():
        sqlite_store.write_companies(rows)


def read_application_contacts():
    if using_sqlite():
        return sqlite_store.read_application_contacts()
    return []


def link_application_contact(application_id, contact_id):
    if using_sqlite():
        return sqlite_store.link_application_contact(application_id, contact_id)
    return {"application_id": application_id, "contact_id": contact_id}


def unlink_application_contact(application_id, contact_id):
    if using_sqlite():
        return sqlite_store.unlink_application_contact(application_id, contact_id)
    return {"application_id": application_id, "contact_id": contact_id}


def read_company_contacts():
    if using_sqlite():
        return sqlite_store.read_company_contacts()
    return []


def link_company_contact(company_id, contact_id):
    if using_sqlite():
        return sqlite_store.link_company_contact(company_id, contact_id)
    return {"company_id": company_id, "contact_id": contact_id}


def unlink_company_contact(company_id, contact_id):
    if using_sqlite():
        return sqlite_store.unlink_company_contact(company_id, contact_id)
    return {"company_id": company_id, "contact_id": contact_id}


def read_company_career_sources():
    if using_sqlite():
        return sqlite_store.read_company_career_sources()
    return []


def write_company_career_sources(rows):
    if using_sqlite():
        sqlite_store.write_company_career_sources(rows)


def read_company_posting_candidates():
    if using_sqlite():
        return sqlite_store.read_company_posting_candidates()
    return []


def write_company_posting_candidates(rows):
    if using_sqlite():
        sqlite_store.write_company_posting_candidates(rows)


def read_company_career_scans(company_id="", limit=200):
    if using_sqlite():
        return sqlite_store.read_company_career_scans(company_id, limit)
    return []


def write_company_career_scan(row):
    if using_sqlite():
        return sqlite_store.write_company_career_scan(row)
    return row


def clear_company_career_scans():
    if using_sqlite():
        sqlite_store.clear_company_career_scans()


def read_posting_note(application_id):
    if using_sqlite():
        return sqlite_store.read_posting_note(application_id)
    return None


def write_posting_note(application_id, path, content):
    if using_sqlite():
        return sqlite_store.write_posting_note(application_id, path, content)
    note_path = paths.ROOT / path
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text(content or "", encoding="utf-8")
    return {"application_id": application_id, "path": path, "content": content or ""}
