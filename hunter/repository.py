"""Active Hunter data repository.

SQLite is the only runtime store. CSV is an explicit import/export format.
"""

from . import posting_snapshots, sqlite_store


def using_sqlite():
    sqlite_store.ensure_initialized()
    return True


def backend_name():
    return "sqlite"


def data_revision():
    return sqlite_store.data_revision()


def read_applications():
    return sqlite_store.read_applications()


def save_applications_changes(rows):
    sqlite_store.save_applications_changes(rows)
    return


def delete_unmodified_discovery_application(application_id):
    return sqlite_store.delete_unmodified_discovery_application(application_id)


def read_actions():
    return sqlite_store.read_actions()


def save_actions_changes(rows):
    sqlite_store.save_actions_changes(rows)
    return


def read_contacts():
    return sqlite_store.read_contacts()


def save_contacts_changes(rows):
    sqlite_store.save_contacts_changes(rows)
    return


def read_companies():
    return sqlite_store.read_companies()


def write_companies(rows):
    sqlite_store.write_companies(rows)


def replace_companies_for_import(rows):
    sqlite_store.replace_companies_for_import(rows)


def read_company(company_id):
    return sqlite_store.read_company(company_id)


def upsert_companies(rows):
    return sqlite_store.upsert_companies(rows)


def insert_companies(rows):
    return sqlite_store.insert_companies(rows)


def update_company_fields(company_id, updates):
    return sqlite_store.update_company_fields(company_id, updates)


def bulk_update_company_fields(updates_by_id):
    return sqlite_store.bulk_update_company_fields(updates_by_id)


def delete_company(company_id):
    return sqlite_store.delete_company(company_id)


def merge_company_references(keep_company_id, merge_company_id, company_name):
    sqlite_store.merge_company_references(
        keep_company_id,
        merge_company_id,
        company_name,
    )


def merge_companies_atomic(keep_row, merge_company_id):
    return sqlite_store.merge_companies_atomic(keep_row, merge_company_id)


def read_application_contacts():
    return sqlite_store.read_application_contacts()


def link_application_contact(application_id, contact_id):
    return sqlite_store.link_application_contact(application_id, contact_id)


def unlink_application_contact(application_id, contact_id):
    return sqlite_store.unlink_application_contact(application_id, contact_id)


def read_company_contacts():
    return sqlite_store.read_company_contacts()


def link_company_contact(company_id, contact_id):
    return sqlite_store.link_company_contact(company_id, contact_id)


def unlink_company_contact(company_id, contact_id):
    return sqlite_store.unlink_company_contact(company_id, contact_id)


def read_company_career_sources():
    return sqlite_store.read_company_career_sources()


def write_company_career_sources(rows):
    sqlite_store.write_company_career_sources(rows)


def upsert_company_career_source(row):
    return sqlite_store.upsert_company_career_source(row)


def read_company_posting_candidates():
    return sqlite_store.read_company_posting_candidates()


def write_company_posting_candidates(rows):
    sqlite_store.write_company_posting_candidates(rows)


def replace_company_posting_candidates_for_import(rows):
    sqlite_store.replace_company_posting_candidates_for_import(rows)


def read_company_posting_candidates_for_company(company_id):
    return sqlite_store.read_company_posting_candidates_for_company(company_id)


def read_company_posting_candidate(candidate_id):
    return sqlite_store.read_company_posting_candidate(candidate_id)


def upsert_company_posting_candidates(rows):
    return sqlite_store.upsert_company_posting_candidates(rows)


def insert_company_posting_candidates(rows):
    return sqlite_store.insert_company_posting_candidates(rows)


def update_company_posting_candidate_fields(candidate_id, updates):
    return sqlite_store.update_company_posting_candidate_fields(candidate_id, updates)


def compare_and_update_company_posting_candidate_fields(candidate_id, expected, updates):
    return sqlite_store.compare_and_update_company_posting_candidate_fields(
        candidate_id,
        expected,
        updates,
    )


def bulk_update_company_posting_candidate_fields(updates_by_id):
    return sqlite_store.bulk_update_company_posting_candidate_fields(updates_by_id)


def update_company_posting_candidate_statuses(candidate_ids, status):
    return sqlite_store.update_company_posting_candidate_statuses(candidate_ids, status)


def read_company_career_scans(company_id="", limit=200):
    return sqlite_store.read_company_career_scans(company_id, limit)


def write_company_career_scan(row):
    return sqlite_store.write_company_career_scan(row)


def clear_company_career_scans():
    sqlite_store.clear_company_career_scans()


def read_discovery_searches():
    return sqlite_store.read_discovery_searches()


def save_discovery_searches_changes(rows):
    sqlite_store.save_discovery_searches_changes(rows)


def read_discovery_candidates():
    return sqlite_store.read_discovery_candidates()


def read_discovery_candidates_snapshot():
    return sqlite_store.read_discovery_candidates_snapshot()


def reconcile_discovery_candidates(expected_revision, rows):
    return sqlite_store.reconcile_discovery_candidates(expected_revision, rows)


def write_discovery_candidates(rows):
    sqlite_store.write_discovery_candidates(rows)


def replace_discovery_candidates_for_import(rows):
    sqlite_store.replace_discovery_candidates_for_import(rows)


def read_discovery_candidate(candidate_id):
    return sqlite_store.read_discovery_candidate(candidate_id)


def upsert_discovery_candidates(rows):
    return sqlite_store.upsert_discovery_candidates(rows)


def insert_discovery_candidates(rows):
    return sqlite_store.insert_discovery_candidates(rows)


def update_discovery_candidate_fields(candidate_id, updates):
    return sqlite_store.update_discovery_candidate_fields(candidate_id, updates)


def compare_and_update_discovery_candidate_fields(candidate_id, expected, updates):
    return sqlite_store.compare_and_update_discovery_candidate_fields(
        candidate_id,
        expected,
        updates,
    )


def bulk_update_discovery_candidate_fields(updates_by_id):
    return sqlite_store.bulk_update_discovery_candidate_fields(updates_by_id)


def update_discovery_candidate_statuses(candidate_ids, status, **status_fields):
    return sqlite_store.update_discovery_candidate_statuses(
        candidate_ids,
        status,
        **status_fields,
    )


def read_suggestion_dismissals():
    return sqlite_store.read_suggestion_dismissals()


def dismiss_suggestion(suggestion_id, dismissed_at):
    return sqlite_store.dismiss_suggestion(suggestion_id, dismissed_at)


def restore_suggestion(suggestion_id):
    return sqlite_store.restore_suggestion(suggestion_id)


def read_posting_note(application_id):
    return sqlite_store.read_posting_note(application_id)


def write_posting_note(application_id, path, content):
    return sqlite_store.write_posting_note(application_id, path, content)


def read_posting_snapshots(application_id=""):
    snapshots = sqlite_store.read_posting_snapshots(application_id)
    for snapshot in snapshots:
        snapshot["content_text"] = posting_snapshots.readable_content(
            snapshot.get("final_url") or snapshot.get("source_url"),
            snapshot.get("content_text", ""),
            snapshot.get("source_html", ""),
        )
    return snapshots


def write_posting_snapshot(application_id, values):
    return sqlite_store.write_posting_snapshot(application_id, values)


def read_resume_versions(application_id=""):
    return sqlite_store.read_resume_versions(application_id)


def write_resume_version(row):
    return sqlite_store.write_resume_version(row)


def replace_applications_for_import(rows):
    sqlite_store.replace_applications_for_import(rows)


def replace_actions_for_import(rows):
    sqlite_store.replace_actions_for_import(rows)


def replace_contacts_for_import(rows):
    sqlite_store.replace_contacts_for_import(rows)


def replace_discovery_searches_for_import(rows):
    sqlite_store.replace_discovery_searches_for_import(rows)
