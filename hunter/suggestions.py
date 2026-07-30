"""Persistent review state for Hunter's proactive suggestions."""

from datetime import datetime

from . import repository, storage


def dismissed_ids():
    return {
        row.get("suggestion_id", "")
        for row in repository.read_suggestion_dismissals()
        if row.get("suggestion_id", "")
    }


def dismiss(suggestion_id):
    cleaned_id = storage.clean(suggestion_id)
    if not cleaned_id:
        raise ValueError("Suggestion id is required.")
    if len(cleaned_id) > 240:
        raise ValueError("Suggestion id is too long.")
    return repository.dismiss_suggestion(
        cleaned_id,
        datetime.now().isoformat(timespec="seconds"),
    )


def restore(suggestion_id):
    cleaned_id = storage.clean(suggestion_id)
    if not cleaned_id:
        raise ValueError("Suggestion id is required.")
    return repository.restore_suggestion(cleaned_id)
