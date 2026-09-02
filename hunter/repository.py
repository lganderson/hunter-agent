"""Active Hunter data repository.

CSV remains an import/export format. Once the SQLite database is initialized,
runtime reads and writes use SQLite as the local app store.
"""

from . import paths, posting_snapshots, schema, sqlite_store, storage


def using_sqlite():
    return sqlite_store.is_initialized()


def backend_name():
    return "sqlite" if using_sqlite() else "csv"


def data_revision():
    return sqlite_store.data_revision() if using_sqlite() else 0


def read_applications():
    if using_sqlite():
        return sqlite_store.read_applications()
    return storage.read_rows(paths.APPLICATIONS, schema.APPLICATION_FIELDS)


def write_applications(rows):
    if using_sqlite():
        sqlite_store.write_applications(rows)
        return
    storage.write_rows(paths.APPLICATIONS, schema.APPLICATION_FIELDS, rows)


def delete_unmodified_discovery_application(application_id):
    if using_sqlite():
        return sqlite_store.delete_unmodified_discovery_application(application_id)
    raise ValueError("Undo Consider requires the local SQLite store.")


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


def replace_companies_for_import(rows):
    if using_sqlite():
        sqlite_store.replace_companies_for_import(rows)


def read_company(company_id):
    if using_sqlite():
        return sqlite_store.read_company(company_id)
    return None


def upsert_companies(rows):
    if using_sqlite():
        return sqlite_store.upsert_companies(rows)
    return []


def insert_companies(rows):
    if using_sqlite():
        return sqlite_store.insert_companies(rows)
    return []


def update_company_fields(company_id, updates):
    if using_sqlite():
        return sqlite_store.update_company_fields(company_id, updates)
    raise ValueError("Company updates require the local SQLite store.")


def bulk_update_company_fields(updates_by_id):
    if using_sqlite():
        return sqlite_store.bulk_update_company_fields(updates_by_id)
    raise ValueError("Company updates require the local SQLite store.")


def delete_company(company_id):
    if using_sqlite():
        return sqlite_store.delete_company(company_id)
    raise ValueError("Company deletion requires the local SQLite store.")


def merge_company_references(keep_company_id, merge_company_id, company_name):
    if using_sqlite():
        sqlite_store.merge_company_references(
            keep_company_id,
            merge_company_id,
            company_name,
        )


def merge_companies_atomic(keep_row, merge_company_id):
    if using_sqlite():
        return sqlite_store.merge_companies_atomic(keep_row, merge_company_id)
    raise ValueError("Company merging requires the local SQLite store.")


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


def upsert_company_career_source(row):
    if using_sqlite():
        return sqlite_store.upsert_company_career_source(row)
    return row


def read_company_posting_candidates():
    if using_sqlite():
        return sqlite_store.read_company_posting_candidates()
    return []


def write_company_posting_candidates(rows):
    if using_sqlite():
        sqlite_store.write_company_posting_candidates(rows)


def replace_company_posting_candidates_for_import(rows):
    if using_sqlite():
        sqlite_store.replace_company_posting_candidates_for_import(rows)


def read_company_posting_candidate(candidate_id):
    if using_sqlite():
        return sqlite_store.read_company_posting_candidate(candidate_id)
    return None


def upsert_company_posting_candidates(rows):
    if using_sqlite():
        return sqlite_store.upsert_company_posting_candidates(rows)
    return []


def insert_company_posting_candidates(rows):
    if using_sqlite():
        return sqlite_store.insert_company_posting_candidates(rows)
    return []


def update_company_posting_candidate_fields(candidate_id, updates):
    if using_sqlite():
        return sqlite_store.update_company_posting_candidate_fields(candidate_id, updates)
    raise ValueError("Company candidate updates require the local SQLite store.")


def compare_and_update_company_posting_candidate_fields(candidate_id, expected, updates):
    if using_sqlite():
        return sqlite_store.compare_and_update_company_posting_candidate_fields(
            candidate_id,
            expected,
            updates,
        )
    raise ValueError("Company candidate updates require the local SQLite store.")


def bulk_update_company_posting_candidate_fields(updates_by_id):
    if using_sqlite():
        return sqlite_store.bulk_update_company_posting_candidate_fields(updates_by_id)
    raise ValueError("Company candidate updates require the local SQLite store.")


def update_company_posting_candidate_statuses(candidate_ids, status):
    if using_sqlite():
        return sqlite_store.update_company_posting_candidate_statuses(candidate_ids, status)
    raise ValueError("Company candidate updates require the local SQLite store.")


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


def read_discovery_searches():
    if using_sqlite():
        return sqlite_store.read_discovery_searches()
    return []


def write_discovery_searches(rows):
    if using_sqlite():
        sqlite_store.write_discovery_searches(rows)


def read_discovery_candidates():
    if using_sqlite():
        return sqlite_store.read_discovery_candidates()
    return []


def read_discovery_candidates_snapshot():
    if using_sqlite():
        return sqlite_store.read_discovery_candidates_snapshot()
    return 0, []


def reconcile_discovery_candidates(expected_revision, rows):
    if using_sqlite():
        return sqlite_store.reconcile_discovery_candidates(expected_revision, rows)
    raise ValueError("Discovery candidate reconciliation requires the local SQLite store.")


def write_discovery_candidates(rows):
    if using_sqlite():
        sqlite_store.write_discovery_candidates(rows)


def replace_discovery_candidates_for_import(rows):
    if using_sqlite():
        sqlite_store.replace_discovery_candidates_for_import(rows)


def read_discovery_candidate(candidate_id):
    if using_sqlite():
        return sqlite_store.read_discovery_candidate(candidate_id)
    return None


def upsert_discovery_candidates(rows):
    if using_sqlite():
        return sqlite_store.upsert_discovery_candidates(rows)
    return []


def insert_discovery_candidates(rows):
    if using_sqlite():
        return sqlite_store.insert_discovery_candidates(rows)
    return []


def update_discovery_candidate_fields(candidate_id, updates):
    if using_sqlite():
        return sqlite_store.update_discovery_candidate_fields(candidate_id, updates)
    raise ValueError("Discovery candidate updates require the local SQLite store.")


def compare_and_update_discovery_candidate_fields(candidate_id, expected, updates):
    if using_sqlite():
        return sqlite_store.compare_and_update_discovery_candidate_fields(
            candidate_id,
            expected,
            updates,
        )
    raise ValueError("Discovery candidate updates require the local SQLite store.")


def bulk_update_discovery_candidate_fields(updates_by_id):
    if using_sqlite():
        return sqlite_store.bulk_update_discovery_candidate_fields(updates_by_id)
    raise ValueError("Discovery candidate updates require the local SQLite store.")


def update_discovery_candidate_statuses(candidate_ids, status, **status_fields):
    if using_sqlite():
        return sqlite_store.update_discovery_candidate_statuses(
            candidate_ids,
            status,
            **status_fields,
        )
    raise ValueError("Discovery candidate updates require the local SQLite store.")


def read_suggestion_dismissals():
    return sqlite_store.read_suggestion_dismissals()


def dismiss_suggestion(suggestion_id, dismissed_at):
    return sqlite_store.dismiss_suggestion(suggestion_id, dismissed_at)


def restore_suggestion(suggestion_id):
    return sqlite_store.restore_suggestion(suggestion_id)


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


def read_posting_snapshots(application_id=""):
    if using_sqlite():
        snapshots = sqlite_store.read_posting_snapshots(application_id)
        for snapshot in snapshots:
            snapshot["content_text"] = posting_snapshots.readable_content(
                snapshot.get("final_url") or snapshot.get("source_url"),
                snapshot.get("content_text", ""),
                snapshot.get("source_html", ""),
            )
        return snapshots
    return []


def write_posting_snapshot(application_id, values):
    if using_sqlite():
        return sqlite_store.write_posting_snapshot(application_id, values)
    return None


def read_resume_versions(application_id=""):
    if using_sqlite():
        return sqlite_store.read_resume_versions(application_id)
    return []


def write_resume_version(row):
    if not using_sqlite():
        raise ValueError("Resume versions require the local SQLite store.")
    return sqlite_store.write_resume_version(row)
