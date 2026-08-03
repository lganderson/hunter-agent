"""Navigation-safe background jobs for company discovery."""

import json
import threading
import uuid
from datetime import datetime

from . import company_discovery, paths, storage


JOB_FILE_NAME = "company_discovery_job.json"
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
                        "message": "Company discovery stopped because the local server restarted.",
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


def _normalized_request(payload):
    return {
        "focus": storage.clean(payload.get("focus", "")) or company_discovery.DEFAULT_FOCUS,
        "sizes": company_discovery.normalized_sizes(payload.get("sizes", [])),
        "sources": company_discovery.normalized_sources(payload.get("sources", [])),
        "locations": company_discovery.normalized_location_preferences(payload.get("locations", [])),
        "remote_region": storage.clean(payload.get("remote_region", "")) or company_discovery.DEFAULT_REMOTE_REGION,
        "metro_area": storage.clean(payload.get("metro_area", "")) or company_discovery.DEFAULT_METRO_AREA,
    }


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
