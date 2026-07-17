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
    "website",
    "careers_url",
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
    {"id": "needs-direct-url", "label": "Needs direct URL", "sort_order": "10", "is_terminal": "", "is_active": "1"},
    {"id": "posting-review", "label": "Posting review", "sort_order": "20", "is_terminal": "", "is_active": "1"},
    {"id": "resume-tailoring", "label": "Resume tailoring", "sort_order": "30", "is_terminal": "", "is_active": "1"},
    {"id": "ready-to-apply", "label": "Ready to apply", "sort_order": "40", "is_terminal": "", "is_active": "1"},
    {"id": "application-submitted", "label": "Application submitted", "sort_order": "50", "is_terminal": "", "is_active": "1"},
    {"id": "waiting-response", "label": "Waiting response", "sort_order": "60", "is_terminal": "", "is_active": "1"},
    {"id": "recruiter-screen", "label": "Recruiter screen", "sort_order": "70", "is_terminal": "", "is_active": "1"},
    {"id": "first-interview", "label": "First interview", "sort_order": "80", "is_terminal": "", "is_active": "1"},
    {"id": "second-interview", "label": "Second interview", "sort_order": "90", "is_terminal": "", "is_active": "1"},
    {"id": "final-interview", "label": "Final interview", "sort_order": "100", "is_terminal": "", "is_active": "1"},
    {"id": "offer-review", "label": "Offer review", "sort_order": "110", "is_terminal": "", "is_active": "1"},
    {"id": "closed", "label": "Closed", "sort_order": "120", "is_terminal": "1", "is_active": "1"},
]

DEFAULT_WORKFLOW_ACTION_TYPES = [
    {"id": "find-canonical-posting", "label": "Find canonical posting", "description": "Find the employer's direct careers-page posting before applying from an aggregator.", "default_priority": "medium", "default_due_days": "1", "allowed_stages": "needs-direct-url,posting-review", "sort_order": "10", "is_active": "1"},
    {"id": "verify-source", "label": "Verify source", "description": "Open the posting in the browser and confirm active status, location, compensation, and apply button.", "default_priority": "medium", "default_due_days": "1", "allowed_stages": "needs-direct-url,posting-review", "sort_order": "20", "is_active": "1"},
    {"id": "review-fit", "label": "Review fit", "description": "Review the role, decide positioning, and identify whether to apply.", "default_priority": "medium", "default_due_days": "1", "allowed_stages": "posting-review", "sort_order": "30", "is_active": "1"},
    {"id": "company-research", "label": "Company research", "description": "Research the company, team, product, and hiring context.", "default_priority": "medium", "default_due_days": "2", "allowed_stages": "posting-review,resume-tailoring,ready-to-apply,application-submitted,waiting-response,interviewing,recruiter-screen,first-interview,second-interview,final-interview,offer-review", "sort_order": "40", "is_active": "1"},
    {"id": "tailor-resume", "label": "Tailor resume", "description": "Tailor the resume for this posting.", "default_priority": "medium", "default_due_days": "1", "allowed_stages": "resume-tailoring,ready-to-apply", "sort_order": "50", "is_active": "1"},
    {"id": "draft-cover-letter", "label": "Draft cover letter", "description": "Draft a focused cover letter or application note.", "default_priority": "medium", "default_due_days": "1", "allowed_stages": "resume-tailoring,ready-to-apply", "sort_order": "60", "is_active": "1"},
    {"id": "draft-application-answer", "label": "Draft application answer", "description": "Draft a required application answer for this posting.", "default_priority": "high", "default_due_days": "1", "allowed_stages": "resume-tailoring,ready-to-apply", "sort_order": "70", "is_active": "1"},
    {"id": "submit-application", "label": "Submit application", "description": "Submit the application through the employer's application flow.", "default_priority": "high", "default_due_days": "1", "allowed_stages": "ready-to-apply", "sort_order": "80", "is_active": "1"},
    {"id": "follow-up", "label": "Follow up", "description": "Follow up on a submitted application or conversation.", "default_priority": "medium", "default_due_days": "7", "allowed_stages": "application-submitted,waiting-response,recruiter-screen,first-interview,second-interview,final-interview", "sort_order": "90", "is_active": "1"},
    {"id": "prep-interview", "label": "Prep interview", "description": "Prepare for an interview or screen.", "default_priority": "high", "default_due_days": "2", "allowed_stages": "recruiter-screen,first-interview,second-interview,final-interview", "sort_order": "100", "is_active": "1"},
    {"id": "send-thank-you", "label": "Send thank you", "description": "Send a thank-you or follow-up note after an interview.", "default_priority": "medium", "default_due_days": "1", "allowed_stages": "recruiter-screen,first-interview,second-interview,final-interview", "sort_order": "110", "is_active": "1"},
    {"id": "review-offer", "label": "Review offer", "description": "Review offer details, questions, and negotiation points.", "default_priority": "high", "default_due_days": "2", "allowed_stages": "offer-review", "sort_order": "120", "is_active": "1"},
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

DEFAULT_STAGE = "posting-review"
DEFAULT_OUTCOME = ""
DEFAULT_PRIORITY = "medium"
DEFAULT_COMPANY_INTEREST_STATUS = "neutral"
COMPANY_INTEREST_STATUSES = {"interested", "neutral", "archived"}
COMPANY_POSTING_CANDIDATE_STATUSES = {"new", "ignored", "ingested", "unavailable"}
COMPANY_POSTING_CANDIDATE_SCAN_STATES = {"current", "not-seen", "verification-pending", "unavailable"}
