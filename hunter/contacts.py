"""Contact management operations for Hunter."""

import re

from . import repository, schema, storage


EDITABLE_FIELDS = {
    "name",
    "company",
    "role",
    "email",
    "linkedin",
    "relationship",
    "status",
    "last_contacted",
    "next_follow_up",
    "notes",
}


def next_contact_id(rows):
    highest = 0
    for row in rows:
        match = re.fullmatch(r"C(\d+)", row.get("id", "").upper())
        if match:
            highest = max(highest, int(match.group(1)))
    return f"C{highest + 1:04d}"


def list_contacts():
    return repository.read_contacts()


def upsert_contact(contact_id="", updates=None):
    rows = repository.read_contacts()
    wanted = storage.clean(contact_id).upper()
    row = None
    if wanted:
        row = next((item for item in rows if item.get("id", "").upper() == wanted), None)
        if row is None:
            raise ValueError(f"No contact found with id {contact_id}.")
    if row is None:
        row = {field: "" for field in schema.CONTACT_FIELDS}
        row["id"] = next_contact_id(rows)
        rows.append(row)

    for field, value in (updates or {}).items():
        if field not in EDITABLE_FIELDS:
            continue
        if field in {"last_contacted", "next_follow_up"}:
            row[field] = storage.normalize_date(value)
        else:
            row[field] = storage.clean(value)

    repository.write_contacts(rows)
    return row


def link_contact(application_id, contact_id):
    application_id = storage.clean(application_id).upper()
    contact_id = storage.clean(contact_id).upper()
    if not any(app.get("id", "").upper() == application_id for app in repository.read_applications()):
        raise ValueError(f"No posting found with id {application_id}.")
    if not any(contact.get("id", "").upper() == contact_id for contact in repository.read_contacts()):
        raise ValueError(f"No contact found with id {contact_id}.")
    return repository.link_application_contact(application_id, contact_id)


def unlink_contact(application_id, contact_id):
    return repository.unlink_application_contact(application_id, contact_id)
