"""Shared table fields and state constants."""

APPLICATION_FIELDS = [
    "id",
    "company_id",
    "company",
    "role",
    "location",
    "work_mode",
    "source",
    "source_url",
    "compensation",
    "stage",
    "outcome",
    "tags",
    "priority",
    "date_found",
    "date_applied",
    "next_action_id",
    "next_action",
    "next_action_date",
    "contact",
    "resume_version",
    "cover_letter",
    "posting_file",
    "notes",
]

POSTING_SNAPSHOT_FIELDS = [
    "id",
    "application_id",
    "source_url",
    "final_url",
    "captured_at",
    "http_status",
    "capture_method",
    "capture_model",
    "sources_json",
    "content_hash",
    "content_text",
    "source_html",
    "warnings",
]

RESUME_VERSION_FIELDS = [
    "id",
    "application_id",
    "created_at",
    "guidance",
    "source_filename",
    "docx_path",
    "pdf_path",
    "changes_json",
    "warnings_json",
]

CONTACT_FIELDS = [
    "id",
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
]

COMPANY_FIELDS = [
    "id",
    "name",
    "aliases",
    "interest_status",
    "tracking_status",
    "discovered_at",
    "last_seen_at",
    "website",
    "careers_url",
    "industry",
    "company_size",
    "company_profile_url",
    "company_metadata_source",
    "company_metadata_checked_at",
    "company_metadata_suggestions_json",
    "company_research_status",
    "company_discovery_source",
    "company_discovery_source_url",
    "company_discovery_query",
    "company_discovery_evidence",
    "company_location_fit",
    "company_location",
    "company_remote_policy",
    "company_location_evidence",
    "company_location_checked_at",
    "company_fit_score",
    "company_fit_summary",
    "company_fit_checked_at",
    "company_evaluation_status",
    "company_evaluation_version",
    "company_evaluation_checked_at",
    "company_evaluation_error",
    "notes",
    "last_checked_at",
    "last_check_status",
]

COMPANY_CONTACT_FIELDS = [
    "company_id",
    "contact_id",
    "created_at",
]

COMPANY_CAREER_SOURCE_FIELDS = [
    "company_id",
    "source_url",
    "platform_type",
    "config_json",
    "evidence",
    "discovered_at",
    "last_verified_at",
    "status",
    "notes",
]

COMPANY_POSTING_CANDIDATE_FIELDS = [
    "id",
    "company_id",
    "title",
    "url",
    "location",
    "work_mode",
    "category",
    "source_platform",
    "source_job_id",
    "matched_queries",
    "description_excerpt",
    "description_hash",
    "score_inputs_hash",
    "normalization_warnings",
    "qualification_status",
    "qualification_reason",
    "last_scan_run_id",
    "scan_state",
    "last_verified_at",
    "status",
    "first_seen_at",
    "last_seen_at",
    "fit_score",
    "fit_summary",
    "fit_checked_at",
    "notes",
]

COMPANY_CAREER_SCAN_FIELDS = [
    "company_id",
    "checked_at",
    "run_id",
    "platform_type",
    "status",
    "requests_succeeded",
    "requests_failed",
    "extracted_count",
    "unique_candidate_count",
    "new_count",
    "recommended_count",
    "unavailable_count",
    "verification_count",
    "verification_skipped_count",
    "errors_json",
]

DISCOVERY_SEARCH_FIELDS = [
    "id",
    "name",
    "keywords",
    "location",
    "remote_location",
    "lanes_json",
    "role_family_ids_json",
    "excluded_terms_json",
    "created_at",
    "updated_at",
    "last_opened_at",
    "last_run_at",
    "last_run_summary_json",
]

DISCOVERY_CANDIDATE_FIELDS = [
    "id",
    "search_id",
    "search_ids_json",
    "company_id",
    "title",
    "url",
    "canonical_url",
    "location",
    "work_mode",
    "source_platform",
    "captured_at",
    "last_seen_at",
    "status",
    "processing_status",
    "qualification_status",
    "qualification_reason",
    "fit_score",
    "fit_summary",
    "fit_checked_at",
    "description_text",
    "description_excerpt",
    "warnings",
    "source_urls_json",
    "acquisition_provenance_json",
    "freshness_status",
    "freshness_checked_at",
    "detail_attempt_count",
    "detail_last_attempt_at",
    "detail_last_error",
    "ingested_application_id",
    "ignore_reason",
    "ignore_reason_detail",
    "notes",
]

INTERVIEW_FIELDS = [
    "id",
    "application_id",
    "company",
    "role",
    "stage",
    "scheduled_date",
    "scheduled_time",
    "timezone",
    "participants",
    "prep_file",
    "outcome",
    "notes",
]

ACTION_FIELDS = [
    "id",
    "application_id",
    "company",
    "role",
    "type",
    "title",
    "description",
    "status",
    "priority",
    "due_date",
    "created_date",
    "completed_date",
    "source",
    "related_url",
    "notes",
]

WORKFLOW_STAGE_FIELDS = [
    "id",
    "label",
    "sort_order",
    "is_terminal",
    "is_active",
]

WORKFLOW_ACTION_TYPE_FIELDS = [
    "id",
    "label",
    "description",
    "default_priority",
    "default_due_days",
    "allowed_stages",
    "sort_order",
    "is_active",
]

DEFAULT_WORKFLOW_STAGES = [
    {"id": "considering", "label": "Considering", "sort_order": "10", "is_terminal": "", "is_active": "1"},
    {"id": "applied", "label": "Applied", "sort_order": "20", "is_terminal": "", "is_active": "1"},
    {"id": "recruiter-screen", "label": "Recruiter Screen", "sort_order": "30", "is_terminal": "", "is_active": "1"},
    {"id": "interviewing", "label": "Interviewing", "sort_order": "40", "is_terminal": "", "is_active": "1"},
    {"id": "offer", "label": "Offer", "sort_order": "50", "is_terminal": "", "is_active": "1"},
    {"id": "closed", "label": "Closed", "sort_order": "60", "is_terminal": "1", "is_active": "1"},
]

DEFAULT_WORKFLOW_ACTION_TYPES = [
    {"id": "find-canonical-posting", "label": "Find canonical posting", "description": "Find the employer's direct careers-page posting before applying from an aggregator.", "default_priority": "medium", "default_due_days": "1", "allowed_stages": "considering", "sort_order": "10", "is_active": "1"},
    {"id": "verify-source", "label": "Verify source", "description": "Open the posting in the browser and confirm active status, location, compensation, and apply button.", "default_priority": "medium", "default_due_days": "1", "allowed_stages": "considering", "sort_order": "20", "is_active": "1"},
    {"id": "review-fit", "label": "Review fit", "description": "Review the role, decide positioning, and identify whether to apply.", "default_priority": "medium", "default_due_days": "1", "allowed_stages": "considering", "sort_order": "30", "is_active": "1"},
    {"id": "company-research", "label": "Company research", "description": "Research the company, team, product, and hiring context.", "default_priority": "medium", "default_due_days": "2", "allowed_stages": "considering,applied,recruiter-screen,interviewing,offer", "sort_order": "40", "is_active": "1"},
    {"id": "tailor-resume", "label": "Tailor resume", "description": "Tailor the resume for this posting.", "default_priority": "medium", "default_due_days": "1", "allowed_stages": "considering", "sort_order": "50", "is_active": "1"},
    {"id": "draft-cover-letter", "label": "Draft cover letter", "description": "Draft a focused cover letter or application note.", "default_priority": "medium", "default_due_days": "1", "allowed_stages": "considering", "sort_order": "60", "is_active": "1"},
    {"id": "draft-application-answer", "label": "Draft application answer", "description": "Draft a required application answer for this posting.", "default_priority": "high", "default_due_days": "1", "allowed_stages": "considering", "sort_order": "70", "is_active": "1"},
    {"id": "submit-application", "label": "Submit application", "description": "Submit the application through the employer's application flow.", "default_priority": "high", "default_due_days": "1", "allowed_stages": "considering", "sort_order": "80", "is_active": "1"},
    {"id": "follow-up", "label": "Follow up", "description": "Follow up on a submitted application or conversation.", "default_priority": "medium", "default_due_days": "7", "allowed_stages": "applied,recruiter-screen,interviewing", "sort_order": "90", "is_active": "1"},
    {"id": "prep-interview", "label": "Prep interview", "description": "Prepare for an interview or screen.", "default_priority": "high", "default_due_days": "2", "allowed_stages": "recruiter-screen,interviewing", "sort_order": "100", "is_active": "1"},
    {"id": "send-thank-you", "label": "Send thank you", "description": "Send a thank-you or follow-up note after an interview.", "default_priority": "medium", "default_due_days": "1", "allowed_stages": "recruiter-screen,interviewing", "sort_order": "110", "is_active": "1"},
    {"id": "review-offer", "label": "Review offer", "description": "Review offer details, questions, and negotiation points.", "default_priority": "high", "default_due_days": "2", "allowed_stages": "offer", "sort_order": "120", "is_active": "1"},
    {"id": "log-outcome", "label": "Log outcome", "description": "Record the final outcome and archive remaining context.", "default_priority": "medium", "default_due_days": "1", "allowed_stages": "closed", "sort_order": "130", "is_active": "1"},
]

TERMINAL_OUTCOMES = {"rejected", "withdrawn", "accepted", "declined", "archived", "closed-posting"}
COMPLETED_ACTION_STATUSES = {"done", "cancelled", "skipped"}
ACTION_STATUSES = {"open", "done", "cancelled", "skipped"}
ACTION_STATUS_ALIASES = {"completed": "done"}
ACTION_TYPE_ALIASES = {
    "application-answer": "draft-application-answer",
    "company_research": "company-research",
    "cover_letter": "draft-cover-letter",
    "interview_prep": "prep-interview",
    "research": "company-research",
    "resume": "tailor-resume",
    "resume_update": "tailor-resume",
    "review-posting": "review-fit",
}

WORKFLOW_STAGE_ALIASES = {
    "needs-direct-url": "considering",
    "posting-review": "considering",
    "resume-tailoring": "considering",
    "ready-to-apply": "considering",
    "application-submitted": "applied",
    "waiting-response": "applied",
    "first-interview": "interviewing",
    "second-interview": "interviewing",
    "final-interview": "interviewing",
    "offer-review": "offer",
}
DEFAULT_STAGE = "considering"
DEFAULT_OUTCOME = ""
DEFAULT_PRIORITY = "medium"
DEFAULT_COMPANY_INTEREST_STATUS = "neutral"
COMPANY_INTEREST_STATUSES = {"interested", "neutral", "not-interested", "archived"}
DEFAULT_COMPANY_TRACKING_STATUS = "tracked"
COMPANY_TRACKING_STATUSES = {"discovered", "tracked"}
CANDIDATE_STATUS_ALIASES = {"ingested": "pursued"}
COMPANY_POSTING_CANDIDATE_STATUSES = {"new", "ignored", "pursued", "unavailable"}
COMPANY_POSTING_CANDIDATE_SCAN_STATES = {"current", "not-seen", "verification-pending", "unavailable"}
DISCOVERY_CANDIDATE_STATUSES = {"new", "ignored", "pursued", "duplicate", "unavailable"}
DISCOVERY_PROCESSING_STATUSES = {"ready", "partial", "needs-details"}
