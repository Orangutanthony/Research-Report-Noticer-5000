import importlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from functools import cache


@dataclass(frozen=True)
class Document:
    poster: str
    filename: str
    mimetype: str
    size: int
    posted_ts: str
    message: str
    read: Callable[[], bytes]


@dataclass(frozen=True)
class Classification:
    is_report: bool
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Week:
    week_number: int | None = None
    report_date: date | None = None
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Links:
    urls: tuple[str, ...] | str | None = None
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Summary:
    week: Week
    status: str | None = None
    summary: tuple[str, ...] | str | None = None
    blockers: tuple[str, ...] | str | None = None
    notes: tuple[str, ...] = ()


def load(module, function):
    return getattr(importlib.import_module(module), function)


def document(msg, slack_file):
    from slack_api import download

    # downloaded only if a module asks since most attachments are never read
    @cache
    def read():
        return download(slack_file)
    return Document(
        msg["user"], slack_file.get("name") or "", slack_file.get("mimetype") or "",
        slack_file.get("size", 0), msg["ts"], msg.get("text") or "", read,
    )


def strings(value):
    return isinstance(value, (list, tuple)) and all(isinstance(s, str) for s in value)


def check_classification(result):
    if not isinstance(result, Classification):
        raise RuntimeError(f"classifier returned {type(result).__name__}, not Classification")
    problems = []
    if not isinstance(result.is_report, bool):
        problems.append("is_report must be a bool")
    if not strings(result.notes):
        problems.append("notes must be strings")
    if problems:
        raise RuntimeError(f"classifier broke the contract: {'; '.join(problems)}")


def check_week(result):
    if not isinstance(result, Week):
        raise RuntimeError(f"week finder returned {type(result).__name__}, not Week")
    problems = []
    if result.week_number is not None and (type(result.week_number) is not int or result.week_number < 1):
        problems.append("week_number must be a positive int or None")
    if result.report_date is not None and not isinstance(result.report_date, date):
        problems.append("report_date must be a date or None")
    if not strings(result.notes):
        problems.append("notes must be strings")
    if problems:
        raise RuntimeError(f"week finder broke the contract: {'; '.join(problems)}")


def check_summary(result):
    # imported here so the package loads without a team, which building the app needs
    from config import NO_REPORT, NOT_APPLICABLE, STATUS_OPTIONS

    if not isinstance(result, Summary):
        raise RuntimeError(f"summarizer returned {type(result).__name__}, not Summary")
    check_week(result.week)
    problems = []
    if result.status is not None and (result.status not in STATUS_OPTIONS or result.status == NO_REPORT):
        problems.append(f"status must be None or one of {tuple(s for s in STATUS_OPTIONS if s != NO_REPORT)}")
    for field in ("summary", "blockers"):
        value = getattr(result, field)
        if value is not None and value != NOT_APPLICABLE and not (strings(value) and value and all(value)):
            problems.append(f"{field} must be None, NOT_APPLICABLE or a non-empty list of non-empty strings")
    if not strings(result.notes):
        problems.append("notes must be strings")
    if problems:
        raise RuntimeError(f"summarizer broke the contract: {'; '.join(problems)}")


def check_links(result):
    from config import NOT_APPLICABLE

    if not isinstance(result, Links):
        raise RuntimeError(f"link finder returned {type(result).__name__}, not Links")
    problems = []
    urls = result.urls
    if urls is not None and urls != NOT_APPLICABLE and not (strings(urls) and urls and all(urls)):
        problems.append("urls must be None, NOT_APPLICABLE or a non-empty list of non-empty strings")
    if not strings(result.notes):
        problems.append("notes must be strings")
    if problems:
        raise RuntimeError(f"link finder broke the contract: {'; '.join(problems)}")
