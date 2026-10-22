import json

from config import (
    COL_BLOCKERS, COL_CONFIRMED, COL_RESEARCHER, COL_STATUS, COL_SUMMARY, COL_WEEK,
    LIST_ID, NO_REPORT, NOT_APPLICABLE, STATUS_LABELS, STATUS_OPTIONS,
)
from slack_api import form, post_json
from weeks import week_label

COLUMN_NAMES = {
    COL_WEEK: "Week",
    COL_RESEARCHER: "Researcher",
    COL_SUMMARY: "Summary",
    COL_BLOCKERS: "Blockers",
    COL_STATUS: "Status",
    COL_CONFIRMED: "Confirmed",
}


def rich_text(text):
    return [{
        "type": "rich_text",
        "elements": [{"type": "rich_text_section", "elements": [{"type": "text", "text": text}]}],
    }]


def cell_text(value):
    if value is None or value == NOT_APPLICABLE:
        return value
    return "\n".join(value)


def cell_lines(text):
    if not text or not text.strip():
        return None
    if text.strip() == NOT_APPLICABLE:
        return NOT_APPLICABLE
    return [line for line in text.splitlines() if line.strip()]


def decoded(value):
    return None if value is None else json.loads(value)


def has_content(value):
    # na and no report are placeholders the bot writes rather than text a person wrote
    return bool(value) and str(value).strip() not in (NOT_APPLICABLE, NO_REPORT)


def row_cells(report):
    return {
        COL_WEEK: week_label(report["week"]),
        COL_SUMMARY: cell_text(decoded(report["summary"])),
        COL_BLOCKERS: cell_text(decoded(report["blockers"])),
        COL_STATUS: report["status"],
        COL_RESEARCHER: report["researcher"],
        COL_CONFIRMED: bool(report["confirmed"]),
    }


def item_cells(item):
    fields = {f["column_id"]: f for f in item["fields"] if f.get("column_id")}
    status = (fields.get(COL_STATUS, {}).get("select") or [None])[0]
    return {
        COL_WEEK: fields.get(COL_WEEK, {}).get("text"),
        COL_SUMMARY: fields.get(COL_SUMMARY, {}).get("text"),
        COL_BLOCKERS: fields.get(COL_BLOCKERS, {}).get("text"),
        COL_STATUS: STATUS_LABELS.get(status),
        COL_RESEARCHER: (fields.get(COL_RESEARCHER, {}).get("user") or [None])[0],
        COL_CONFIRMED: bool(fields.get(COL_CONFIRMED, {}).get("checkbox")),
    }


def list_items():
    items, cursor = [], None
    while True:
        params = {"list_id": LIST_ID, "limit": 200}
        if cursor:
            params["cursor"] = cursor
        result = form("slackLists.items.list", params)
        items += result["items"]
        cursor = result.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            return items


def cell_value(column_id, value):
    if column_id == COL_CONFIRMED:
        return {"checkbox": bool(value)}
    if column_id == COL_STATUS:
        return {"select": [STATUS_OPTIONS[value]]}
    if column_id == COL_RESEARCHER:
        return {"user": [value]}
    return {"rich_text": rich_text(value)}


def create_row(report):
    fields = [dict(column_id=cid, **cell_value(cid, val)) for cid, val in row_cells(report).items() if val is not None]
    result = post_json("slackLists.items.create", {"list_id": LIST_ID, "initial_fields": fields})
    return result["item"]["id"]


def update_cells(row_id, changes):
    cells = [dict(row_id=row_id, column_id=cid, **cell_value(cid, val)) for cid, val in changes.items()]
    post_json("slackLists.items.update", {"list_id": LIST_ID, "cells": cells})
