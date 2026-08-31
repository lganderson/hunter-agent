"""Navigation-safe background jobs for company discovery."""

import json
import threading
import uuid
from datetime import datetime

from . import company_discovery, company_evaluation, paths, storage


JOB_FILE_NAME = "company_discovery_job.json"
ACTIVE_STATUSES = {"queued", "running"}
_lock = threading.RLock()
_active_thread = None
_followup_requested = False


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
                        "message": (
                            "Company evaluation stopped because the local server restarted."
                            if job.get("job_type") == "company-evaluation"
                            else "Company discovery stopped because the local server restarted."
                        ),
                        "error": "Local server restarted before the search completed.",
                        "completed_at": now_iso(),
                        "updated_at": now_iso(),
                    }
                )
                _write_locked(job)
        return _copy(job)


def start_job(payload):
    global _active_thread
    with _lock:
        existing = _read_locked()
        if (
            existing
            and existing.get("status") in ACTIVE_STATUSES
            and _active_thread is not None
            and _active_thread.is_alive()
        ):
            raise ValueError("A company discovery search is already running.")

        timestamp = now_iso()
        job = {
            "id": f"CDJ-{uuid.uuid4().hex[:12]}",
            "job_type": "company-discovery",
            "status": "queued",
            "phase": "queued",
            "message": "Company discovery is queued…",
            "completed_steps": 0,
            "total_steps": 1,
            "source": "",
            "started_at": timestamp,
            "updated_at": timestamp,
            "completed_at": "",
            "error": "",
            "request": _normalized_request(payload),
            "result": None,
        }
        _write_locked(job)
        _active_thread = threading.Thread(
            target=_run_job,
            args=(job["id"], job["request"]),
            name=f"hunter-{job['id'].lower()}",
            daemon=True,
        )
        _active_thread.start()
        return _copy(job)


def start_evaluation_job(payload=None):
    """Start a backfill, or queue missing company IDs behind the active job."""
    global _active_thread, _followup_requested
    request = _normalized_evaluation_request(payload or {})
    profile = company_evaluation.save_profile(request["profile"])
    if request["company_ids"]:
        company_evaluation.mark_pending(
            request["company_ids"],
            profile=profile,
            force=request["force"],
        )
    else:
        targets = company_evaluation.evaluation_targets(
            tracking_status=request["tracking_status"],
            profile=profile,
            force=request["force"],
        )
        company_evaluation.mark_pending(
            [row.get("id", "") for row in targets],
            profile=profile,
            force=True,
        )

    with _lock:
        existing = _read_locked()
        if (
            existing
            and existing.get("status") in ACTIVE_STATUSES
            and _active_thread is not None
            and _active_thread.is_alive()
        ):
            _followup_requested = True
            return _copy(existing)
        return _start_evaluation_locked(request)


def enqueue_pending_evaluation(profile=None):
    """Ensure companies marked pending by candidate discovery get a worker."""
    pending_ids = company_evaluation.pending_company_ids()
    if not pending_ids:
        return current_job()
    return start_evaluation_job(
        {
            "company_ids": pending_ids,
            "tracking_status": "",
            "profile": profile or company_evaluation.load_profile(),
            "force": False,
            "reason": "candidate-discovery",
        }
    )


def _start_evaluation_locked(request):
    global _active_thread
    timestamp = now_iso()
    target_count = len(
        company_evaluation.evaluation_targets(
            company_ids=request["company_ids"],
            tracking_status=request["tracking_status"],
            profile=request["profile"],
            force=False,
        )
    )
    job = {
        "id": f"CEJ-{uuid.uuid4().hex[:12]}",
        "job_type": "company-evaluation",
        "status": "queued",
        "phase": "queued",
        "message": f"{target_count} companies are queued for evaluation…",
        "completed_steps": 0,
        "total_steps": max(1, (target_count + company_evaluation.BATCH_SIZE - 1) // company_evaluation.BATCH_SIZE),
        "source": "company-evaluation",
        "started_at": timestamp,
        "updated_at": timestamp,
        "completed_at": "",
        "error": "",
        "request": request,
        "result": None,
    }
    _write_locked(job)
    _active_thread = threading.Thread(
        target=_run_evaluation_job,
        args=(job["id"], request),
        name=f"hunter-{job['id'].lower()}",
        daemon=True,
    )
    _active_thread.start()
    return _copy(job)


def _run_job(job_id, request):
    _update_job(job_id, status="running", phase="preparing", message="Preparing company discovery…")

    def progress(update):
        _update_job(job_id, status="running", **update)

    try:
        result = company_discovery.run_company_discovery(
            focus=request["focus"],
            sizes=request["sizes"],
            sources=request["sources"],
            locations=request["locations"],
            remote_region=request["remote_region"],
            metro_area=request["metro_area"],
            progress=progress,
        )
    except Exception as exc:  # noqa: BLE001 - background failures must remain inspectable.
        _update_job(
            job_id,
            status="failed",
            phase="failed",
            message=f"Company discovery failed: {storage.clean(str(exc))}",
            error=storage.clean(str(exc)),
            completed_at=now_iso(),
        )
        _launch_pending_followup()
        return

    _update_job(
        job_id,
        status="completed",
        phase="complete",
        message=(
            f"Company discovery complete: {result['review_count']} ready and "
            f"{result['location_verification_count']} need location verification."
        ),
        completed_steps=len(request["sources"]) + 1,
        total_steps=len(request["sources"]) + 1,
        result=result,
        completed_at=now_iso(),
    )
    _launch_pending_followup()


def _run_evaluation_job(job_id, request):
    _update_job(
        job_id,
        status="running",
        phase="preparing",
        message="Preparing company evaluation…",
    )

    def progress(update):
        _update_job(job_id, status="running", **update)

    try:
        result = company_evaluation.evaluate_companies(
            company_ids=request["company_ids"],
            tracking_status=request["tracking_status"],
            profile=request["profile"],
            force=False,
            progress=progress,
            reason=request["reason"],
        )
    except Exception as exc:  # noqa: BLE001 - background failures must remain inspectable.
        _update_job(
            job_id,
            status="failed",
            phase="failed",
            message=f"Company evaluation failed: {storage.clean(str(exc))}",
            error=storage.clean(str(exc)),
            completed_at=now_iso(),
        )
        _launch_pending_followup()
        return

    _update_job(
        job_id,
        status="completed",
        phase="complete",
        message=(
            f"Company evaluation complete: {result['ready_count']} ready, "
            f"{result['needs_verification_count']} need verification, and "
            f"{result['failed_count']} failed."
        ),
        completed_steps=max(1, (result["target_count"] + company_evaluation.BATCH_SIZE - 1) // company_evaluation.BATCH_SIZE),
        total_steps=max(1, (result["target_count"] + company_evaluation.BATCH_SIZE - 1) // company_evaluation.BATCH_SIZE),
        result=result,
        completed_at=now_iso(),
    )
    _launch_pending_followup()


def _normalized_request(payload):
    return {
        "focus": storage.clean(payload.get("focus", "")) or company_discovery.DEFAULT_FOCUS,
        "sizes": company_discovery.normalized_sizes(payload.get("sizes", [])),
        "sources": company_discovery.normalized_sources(payload.get("sources", [])),
        "locations": company_discovery.normalized_location_preferences(payload.get("locations", [])),
        "remote_region": storage.clean(payload.get("remote_region", "")) or company_discovery.DEFAULT_REMOTE_REGION,
        "metro_area": storage.clean(payload.get("metro_area", "")) or company_discovery.DEFAULT_METRO_AREA,
    }


def _normalized_evaluation_request(payload):
    profile_payload = payload.get("profile") if isinstance(payload.get("profile"), dict) else payload
    return {
        "company_ids": list(dict.fromkeys(
            storage.clean(value).upper()
            for value in payload.get("company_ids", [])
            if storage.clean(value)
        )),
        "tracking_status": storage.clean(payload.get("tracking_status", "discovered")),
        "profile": company_evaluation.normalize_profile(profile_payload),
        "force": bool(payload.get("force", False)),
        "reason": storage.clean(payload.get("reason", "backfill")) or "backfill",
    }


def _launch_pending_followup():
    global _followup_requested
    if not _followup_requested:
        return
    _followup_requested = False
    pending_ids = company_evaluation.pending_company_ids()
    if not pending_ids:
        return
    request = _normalized_evaluation_request(
        {
            "company_ids": pending_ids,
            "tracking_status": "",
            "profile": company_evaluation.load_profile(),
            "reason": "queued-discovery",
        }
    )
    with _lock:
        _start_evaluation_locked(request)


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
