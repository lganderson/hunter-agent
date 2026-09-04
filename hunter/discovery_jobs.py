"""Navigation-safe background work for role Discovery."""

import json
import threading
import uuid
from datetime import datetime

from . import discovery, paths, storage


JOB_FILE_NAME = "candidate_enrichment_job.json"
ACTIVE_STATUSES = {"queued", "running"}
_lock = threading.RLock()
_active_thread = None


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def job_path():
    return paths.DATA_DIR / JOB_FILE_NAME


def current_job():
    with _lock:
        job = _read_locked()
        if job and job.get("status") in ACTIVE_STATUSES:
            if _active_thread is None or not _active_thread.is_alive():
                job.update(
                    {
                        "status": "failed",
                        "phase": "interrupted",
                        "message": "Candidate Discovery stopped because the local server restarted.",
                        "error": "Local server restarted before the background job completed.",
                        "completed_at": now_iso(),
                        "updated_at": now_iso(),
                    }
                )
                _write_locked(job)
        return _copy(job)


def start_job(payload=None):
    global _active_thread
    raw_limit = (payload or {}).get("limit")
    request = {
        "search_id": storage.clean((payload or {}).get("search_id", "")).upper(),
        "candidate_id": storage.clean((payload or {}).get("candidate_id", "")).upper(),
        # An omitted limit refreshes the complete eligible backlog. Callers may
        # still request a smaller batch when they need a bounded operation.
        "limit": max(1, int(raw_limit)) if raw_limit is not None else 0,
    }
    targets = discovery.detail_enrichment_targets(
        search_id=request["search_id"],
        candidate_id=request["candidate_id"],
    )
    target_count = min(len(targets), request["limit"]) if request["limit"] else len(targets)
    with _lock:
        existing = _read_locked()
        if (
            existing
            and existing.get("status") in ACTIVE_STATUSES
            and _active_thread is not None
            and _active_thread.is_alive()
        ):
            return _copy(existing)
        timestamp = now_iso()
        job = {
            "id": f"DEJ-{uuid.uuid4().hex[:12]}",
            "job_type": "candidate-enrichment",
            "status": "queued",
            "phase": "queued",
            "message": f"{target_count} existing candidates are queued for posting checks…",
            "completed_steps": 0,
            "total_steps": max(1, target_count),
            "source": "candidate-enrichment",
            "started_at": timestamp,
            "updated_at": timestamp,
            "completed_at": "",
            "error": "",
            "request": request,
            "result": None,
        }
        _write_locked(job)
        _active_thread = threading.Thread(
            target=_run_job,
            args=(job["id"], request),
            name=f"hunter-{job['id'].lower()}",
            daemon=True,
        )
        _active_thread.start()
        return _copy(job)


def start_search_job(payload=None):
    """Start API-first candidate discovery without tying the work to a page request."""
    global _active_thread
    request = {
        "search_id": storage.clean((payload or {}).get("search_id", "")).upper(),
        "enrichment_limit": max(
            0,
            min(250, int((payload or {}).get("enrichment_limit", 100))),
        ),
    }
    # Validate before creating a durable job users cannot act on.
    discovery.get_search(request["search_id"])
    with _lock:
        existing = _read_locked()
        if (
            existing
            and existing.get("status") in ACTIVE_STATUSES
            and _active_thread is not None
            and _active_thread.is_alive()
        ):
            return _copy(existing)
        timestamp = now_iso()
        job = {
            "id": f"DSJ-{uuid.uuid4().hex[:12]}",
            "job_type": "candidate-discovery",
            "status": "queued",
            "phase": "queued",
            "message": "Candidate Discovery is queued with ATS, OpenAI, and Adzuna…",
            "completed_steps": 0,
            "total_steps": 1,
            "source": "candidate-discovery",
            "started_at": timestamp,
            "updated_at": timestamp,
            "completed_at": "",
            "error": "",
            "request": request,
            "result": None,
        }
        _write_locked(job)
        _active_thread = threading.Thread(
            target=_run_search_job,
            args=(job["id"], request),
            name=f"hunter-{job['id'].lower()}",
            daemon=True,
        )
        _active_thread.start()
        return _copy(job)


def _run_job(job_id, request):
    _update_job(
        job_id,
        status="running",
        phase="preparing",
        message="Preparing existing candidate posting checks…",
    )

    def progress(update):
        _update_job(job_id, status="running", **update)

    try:
        result = discovery.enrich_candidate_backlog(
            search_id=request["search_id"],
            candidate_id=request["candidate_id"],
            limit=request["limit"],
            progress=progress,
        )
    except Exception as exc:  # noqa: BLE001 - background failures must remain inspectable.
        _update_job(
            job_id,
            status="failed",
            phase="failed",
            message=f"Candidate enrichment failed: {storage.clean(str(exc))}",
            error=storage.clean(str(exc)),
            completed_at=now_iso(),
        )
        return

    # Detail resolution can identify or create companies that were not available
    # when the search response first queued company evaluation.
    from . import company_discovery_jobs

    try:
        company_discovery_jobs.enqueue_pending_evaluation()
    except Exception as exc:  # noqa: BLE001 - candidate enrichment must still finish durably.
        result.setdefault("errors", []).append(
            f"Company evaluation could not be queued: {storage.clean(str(exc))}"
        )

    _update_job(
        job_id,
        status="completed",
        phase="complete",
        message=(
            f"Posting checks complete: {result['ready_count']} ready for review, "
            f"{result['remaining_count']} can still be checked automatically, and "
            f"{result.get('manual_review_count', result['state_counts']['needs-input'])} need manual review."
        ),
        completed_steps=max(1, result["processed_count"]),
        total_steps=max(1, result["target_count"]),
        result=result,
        completed_at=now_iso(),
    )


def _run_search_job(job_id, request):
    _update_job(
        job_id,
        status="running",
        phase="preparing",
        message="Preparing candidate Discovery providers…",
    )

    def progress(update):
        _update_job(job_id, status="running", **update)

    try:
        result = discovery.continue_discovery(
            request["search_id"],
            enrichment_limit=request["enrichment_limit"],
            progress=progress,
        )
    except Exception as exc:  # noqa: BLE001 - background failures must remain inspectable.
        _update_job(
            job_id,
            status="failed",
            phase="failed",
            message=f"Candidate Discovery failed: {storage.clean(str(exc))}",
            error=storage.clean(str(exc)),
            completed_at=now_iso(),
        )
        return

    from . import company_discovery_jobs

    try:
        company_discovery_jobs.enqueue_pending_evaluation()
    except Exception as exc:  # noqa: BLE001 - candidate results must still finish durably.
        result.setdefault("errors", []).append(
            f"Company evaluation could not be queued: {storage.clean(str(exc))}"
        )

    usable_count = (
        int(result.get("new_count", 0) or 0)
        + int(result.get("updated_count", 0) or 0)
        + int(result.get("associated_count", 0) or 0)
    )
    completion_message = (
        f"Discovery complete: {result.get('new_count', 0)} new roles, "
        f"{result.get('associated_count', 0)} already-known roles added to this search, and "
        f"{result.get('updated_count', 0)} refreshed."
        if usable_count
        else (
            f"Discovery finished with no new eligible roles: {result.get('evaluated_count', 0)} evaluated, "
            f"{result.get('known_count', 0)} already known, and "
            f"{int(result.get('skipped_count', 0) or 0) + int(result.get('screened_count', 0) or 0)} not eligible."
        )
    )

    _update_job(
        job_id,
        status="completed",
        phase="complete",
        message=completion_message,
        completed_steps=max(1, int(result.get("evaluated_count", 0) or 0)),
        total_steps=max(1, int(result.get("evaluated_count", 0) or 0)),
        result=result,
        completed_at=now_iso(),
    )


def _update_job(job_id, **updates):
    with _lock:
        job = _read_locked()
        if not job or job.get("id") != job_id:
            return
        job.update(updates)
        job["updated_at"] = now_iso()
        _write_locked(job)


def _read_locked():
    path = job_path()
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _write_locked(job):
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = job_path()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(job, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _copy(value):
    return json.loads(json.dumps(value)) if value is not None else None
