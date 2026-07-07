#!/usr/bin/env python3
"""Manage Hunter company records."""

import argparse
import json
import sys
from pathlib import Path

ROOT_FOR_IMPORTS = Path(__file__).resolve().parents[1]
if str(ROOT_FOR_IMPORTS) not in sys.path:
    sys.path.insert(0, str(ROOT_FOR_IMPORTS))

from hunter import companies, repository, sqlite_store  # noqa: E402


def print_json(payload):
    print(json.dumps(payload, indent=2, sort_keys=True))


def cmd_list(args):
    sqlite_store.initialize()
    rows = companies.list_companies()
    if args.interest_status:
        rows = [row for row in rows if row.get("interest_status") == args.interest_status]
    if args.search:
        query = args.search.lower()
        rows = [row for row in rows if query in " ".join(row.values()).lower()]
    if not rows:
        print("No companies found.")
        return
    print("ID      STATUS      NAME")
    print("------  ----------  ----")
    for row in rows:
        print(f"{row['id'].ljust(6)}  {row.get('interest_status', '').ljust(10)}  {row.get('name', '')}")


def cmd_get(args):
    sqlite_store.initialize()
    company = companies.get_company(args.company_id)
    company_id = company.get("id", "").upper()
    payload = {
        "company": company,
        "contact_ids": [
            link["contact_id"]
            for link in repository.read_company_contacts()
            if link.get("company_id", "").upper() == company_id
        ],
        "posting_ids": [
            app["id"]
            for app in repository.read_applications()
            if app.get("company_id", "").upper() == company_id
        ],
        "career_source": companies.get_company_career_source(company_id),
        "candidates": companies.candidates_for_company(company_id),
    }
    print_json(payload)


def cmd_upsert(args):
    sqlite_store.initialize()
    updates = {
        "name": args.name,
        "aliases": args.aliases,
        "interest_status": args.interest_status,
        "website": args.website,
        "careers_url": args.careers_url,
        "notes": args.notes,
    }
    company = companies.upsert_company(args.company_id or "", {key: value for key, value in updates.items() if value is not None})
    print(f"Saved {company['id']}: {company['name']}")


def cmd_archive(args):
    sqlite_store.initialize()
    company = companies.archive_company(args.company_id)
    print(f"Archived {company['id']}: {company['name']}")


def cmd_restore(args):
    sqlite_store.initialize()
    company = companies.restore_company(args.company_id, args.interest_status)
    print(f"Restored {company['id']}: {company['name']} ({company['interest_status']})")


def cmd_check(args):
    sqlite_store.initialize()
    try:
        result = companies.check_company_postings(args.company_id)
    except ValueError as exc:
        company = companies.get_company(args.company_id)
        print(f"Could not check {company['name']}: {exc}")
        source = companies.get_company_career_source(company.get("id", ""))
        if source:
            print(f"Career source: {source.get('platform_type') or 'unknown'} ({source.get('status') or 'discovered'})")
        raise SystemExit(1) from None
    print(f"Checked {result['company']['name']}: {result['company']['last_check_status']}")
    source = result.get("career_source") or {}
    if source:
        print(f"Career source: {source.get('platform_type') or 'unknown'} ({source.get('status') or 'discovered'})")
    if result["recommended"]:
        print("Recommended to consider:")
        for candidate in result["recommended"]:
            summary = f" ({candidate['fit_summary']})" if candidate.get("fit_summary") else ""
            print(f"  {candidate['id']}: Fit {candidate.get('fit_score') or '0'} - {candidate['title'] or candidate['url']} - {candidate['url']}{summary}")
    remaining = [candidate for candidate in result["new"] if candidate not in result["recommended"]]
    if remaining:
        print("Other new candidates:")
        for candidate in remaining:
            print(f"  {candidate['id']}: {candidate['title'] or candidate['url']} - {candidate['url']}")


def cmd_link_contact(args):
    sqlite_store.initialize()
    link = companies.link_contact(args.company_id, args.contact_id)
    print(f"Linked {link['contact_id']} to {link['company_id']}")


def cmd_unlink_contact(args):
    sqlite_store.initialize()
    link = companies.unlink_contact(args.company_id, args.contact_id)
    print(f"Unlinked {link['contact_id']} from {link['company_id']}")


def cmd_ingest_candidate(args):
    sqlite_store.initialize()
    result = companies.ingest_candidate(args.candidate_id)
    candidate = result["candidate"]
    print(f"Ingested {candidate['id']}: {candidate['url']}")
    if result.get("stdout"):
        print(result["stdout"])


def build_parser():
    parser = argparse.ArgumentParser(description="Manage Hunter companies.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List companies.")
    list_parser.add_argument("--search", default="")
    list_parser.add_argument("--interest-status", default="")
    list_parser.set_defaults(func=cmd_list)

    get_parser = subparsers.add_parser("get", help="Show one company.")
    get_parser.add_argument("company_id")
    get_parser.set_defaults(func=cmd_get)

    upsert_parser = subparsers.add_parser("upsert", help="Create or update a company.")
    upsert_parser.add_argument("company_id", nargs="?")
    upsert_parser.add_argument("--name")
    upsert_parser.add_argument("--aliases")
    upsert_parser.add_argument("--interest-status")
    upsert_parser.add_argument("--website")
    upsert_parser.add_argument("--careers-url")
    upsert_parser.add_argument("--notes")
    upsert_parser.set_defaults(func=cmd_upsert)

    archive_parser = subparsers.add_parser("archive", help="Archive a company.")
    archive_parser.add_argument("company_id")
    archive_parser.set_defaults(func=cmd_archive)

    restore_parser = subparsers.add_parser("restore", help="Restore an archived company.")
    restore_parser.add_argument("company_id")
    restore_parser.add_argument("--interest-status", choices=["interested", "neutral"], default="neutral")
    restore_parser.set_defaults(func=cmd_restore)

    check_parser = subparsers.add_parser("check", help="Check a company's careers URL.")
    check_parser.add_argument("company_id")
    check_parser.set_defaults(func=cmd_check)

    link_parser = subparsers.add_parser("link-contact", help="Link a contact to a company.")
    link_parser.add_argument("company_id")
    link_parser.add_argument("contact_id")
    link_parser.set_defaults(func=cmd_link_contact)

    unlink_parser = subparsers.add_parser("unlink-contact", help="Unlink a contact from a company.")
    unlink_parser.add_argument("company_id")
    unlink_parser.add_argument("contact_id")
    unlink_parser.set_defaults(func=cmd_unlink_contact)

    ingest_parser = subparsers.add_parser("ingest-candidate", help="Ingest a reviewed company posting candidate.")
    ingest_parser.add_argument("candidate_id")
    ingest_parser.set_defaults(func=cmd_ingest_candidate)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
