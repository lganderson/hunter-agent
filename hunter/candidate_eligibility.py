"""Shared company-interest eligibility rules for candidate review workflows."""

from . import storage


EXCLUDED_COMPANY_INTEREST_STATUSES = frozenset({"not-interested", "archived"})


def companies_by_id(company_rows):
    return {
        storage.clean(company.get("id", "")).upper(): company
        for company in company_rows
        if storage.clean(company.get("id", ""))
    }


def resolve_candidate_company(candidate, company_rows_or_index):
    """Resolve a candidate's explicit company_id without inspecting posting content."""
    index = (
        company_rows_or_index
        if isinstance(company_rows_or_index, dict)
        else companies_by_id(company_rows_or_index)
    )
    return index.get(storage.clean(candidate.get("company_id", "")).upper())


def company_is_excluded(company):
    return bool(
        company
        and storage.clean(company.get("interest_status", "")).lower()
        in EXCLUDED_COMPANY_INTEREST_STATUSES
    )


def candidate_is_excluded(candidate, company_rows_or_index):
    return company_is_excluded(
        resolve_candidate_company(candidate, company_rows_or_index)
    )


def partition_candidates(candidates, company_rows, include_excluded_companies=False):
    """Partition candidates before any review payload or score-derived work is built."""
    index = companies_by_id(company_rows)
    included = []
    excluded = []
    for candidate in candidates:
        if candidate_is_excluded(candidate, index):
            excluded.append(candidate)
        else:
            included.append(candidate)
    return (list(candidates), excluded) if include_excluded_companies else (included, excluded)


def require_candidate_eligible(candidate, company_rows_or_index, operation="review"):
    company = resolve_candidate_company(candidate, company_rows_or_index)
    if company_is_excluded(company):
        status = storage.clean(company.get("interest_status", "")).lower()
        raise ValueError(
            f"Cannot {operation} candidate {candidate.get('id', '')}: "
            f"company {company.get('id', '')} is {status}."
        )
    return company
