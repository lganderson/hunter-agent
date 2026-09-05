"""Request shapes for the local HTTP API; domain rules remain in services."""

from . import schema
from .validation import validate

# Entries describe supplied fields; services own defaults and required business values.
REQUEST_FIELDS = {
    '/api/settings': {'provider': ['string', 'null'], 'model': ['string', 'null'], 'api_base': ['string', 'null'], 'api_token': ['string', 'null'], 'search_goals': ['string', 'null'], 'fit_signals': ['object', 'null'], 'adzuna_app_id': ['string', 'null'], 'adzuna_app_key': ['string', 'null']},
    '/api/settings/resume': {'filename': 'string', 'content_base64': 'string'},
    '/api/resumes/plan': {'application_id': 'string', 'guidance': 'string'},
    '/api/resumes/create': {'application_id': 'string', 'guidance': 'string', 'source_hash': 'string', 'changes': 'array'},
    '/api/actions/generate': {'use_ai': ['boolean', 'null']},
    '/api/postings/archive': {'id': 'string'},
    '/api/postings/archive/manual': {'id': 'string', 'content': 'string'},
    '/api/agent/chat': {'context': 'object', 'api_version': ['integer', 'null'], 'message': ['string', 'null']},
    '/api/suggestions/dismiss': {'id': 'string'},
    '/api/suggestions/restore': {'id': 'string'},
    '/api/actions/update': {'id': 'string', 'status': 'string'},
    '/api/actions/create': {'application_id': 'string', 'values': 'object'},
    '/api/actions/update-fields': {'id': 'string', 'updates': 'object'},
    '/api/actions/make-next': {'id': 'string'},
    '/api/applications/update': {'id': 'string', 'updates': 'object'},
    '/api/applications/create': {'values': 'object'},
    '/api/workflow/stages/archive': {'id': 'string'},
    '/api/workflow/action-types/archive': {'id': 'string'},
    '/api/contacts/upsert': {'id': 'string', 'updates': 'object'},
    '/api/contacts/link': {'application_id': 'string', 'contact_id': 'string'},
    '/api/contacts/unlink': {'application_id': 'string', 'contact_id': 'string'},
    '/api/companies/upsert': {'id': 'string', 'updates': 'object'},
    '/api/companies/archive': {'id': 'string'},
    '/api/companies/restore': {'id': 'string', 'interest_status': 'string'},
    '/api/companies/research': {'id': 'string'},
    '/api/companies/track': {'id': 'string'},
    '/api/companies/untrack': {'id': 'string'},
    '/api/companies/metadata-suggestions/resolve': {'id': 'string', 'suggestion_id': 'string', 'action': 'string'},
    '/api/companies/discover': {'focus': 'string', 'sizes': 'array', 'sources': 'array', 'locations': 'array', 'remote_region': 'string', 'metro_area': 'string'},
    '/api/companies/check': {'id': 'string'},
    '/api/companies/link-contact': {'company_id': 'string', 'contact_id': 'string'},
    '/api/companies/unlink-contact': {'company_id': 'string', 'contact_id': 'string'},
    '/api/companies/merge': {'keep_company_id': 'string', 'merge_company_id': 'string'},
    '/api/companies/candidates/update': {'id': 'string', 'status': 'string'},
    '/api/companies/candidates/bulk-update': {'ids': 'array', 'status': 'string'},
    '/api/discovery/searches/upsert': {'id': 'string', 'updates': 'object'},
    '/api/discovery/searches/apply-exclusions': {'id': 'string', 'excluded_terms': ['string', 'null']},
    '/api/discovery/searches/undo-exclusions': {'candidate_ids': 'array'},
    '/api/discovery/searches/open-linkedin': {'id': 'string'},
    '/api/discovery/searches/run': {'id': 'string'},
    '/api/discovery/continue': {'id': 'string', 'enrichment_limit': 'integer'},
    '/api/discovery/search-jobs': {'id': 'string', 'enrichment_limit': 'integer', 'search_id': 'string'},
    '/api/discovery/candidates/capture': {'search_id': 'string', 'capture_text': 'string', 'details': 'object'},
    '/api/discovery/candidates/details': {'id': 'string', 'updates': 'object'},
    '/api/discovery/candidates/update': {'id': 'string', 'status': 'string', 'ignore_reason': 'string', 'ignore_reason_detail': 'string'},
    '/api/discovery/candidates/bulk-update': {'ids': 'array', 'status': 'string', 'ignore_reason': 'string', 'ignore_reason_detail': 'string'},
    '/api/discovery/candidates/duplicate': {'id': 'string', 'application_id': 'string'},
    '/api/discovery/candidates/undo-decision': {'id': 'string', 'decision': 'string', 'application_id': 'string', 'remove_posting': 'boolean'},
}

STRING_ARRAYS = {"ids", "candidate_ids", "company_ids", "sizes", "sources", "locations",
                 "excluded_terms", "role_family_ids", "work_modes"}
UPDATE_FIELDS = {
    key: {"type": "string"} for key in set(
        schema.APPLICATION_FIELDS + schema.ACTION_FIELDS + schema.CONTACT_FIELDS
        + schema.COMPANY_FIELDS + schema.DISCOVERY_CANDIDATE_FIELDS
    )
}
UPDATE_FIELDS.update({key: {"type": "array", "items": {"type": "string"}} for key in STRING_ARRAYS})
UPDATE_FIELDS["lanes"] = {"type": "array", "items": {"type": "object", "properties": {
    "location": {"type": "string"}, "label": {"type": "string"},
    "work_modes": {"type": "array", "items": {"type": "string"}}
}}}


def validate_request(path, payload):
    properties = {key: {"type": kind} for key, kind in REQUEST_FIELDS.get(path, {}).items()}
    for key in {"updates", "values", "details"} & properties.keys():
        properties[key] = {"type": "object", "properties": UPDATE_FIELDS}
    for key in STRING_ARRAYS & properties.keys():
        properties[key] = {"type": "array", "items": {"type": "string"}}
    for key in {"messages", "changes"} & properties.keys():
        properties[key] = {"type": "array", "items": {"type": "object"}}
    return validate(payload, {"type": "object", "properties": properties})
