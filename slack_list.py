import json

from config import (
    COL_BLOCKERS, COL_CONFIRMED, COL_LINK, COL_RESEARCHER, COL_STATUS, COL_SUMMARY, COL_WEEK, COLUMN_TYPES,
    LIST_ID, NO_REPORT, NOT_APPLICABLE, RESEARCHER_NAMES, STATUS_LABELS, STATUS_OPTIONS, WEEK_DAY,
)
from slack_api import form, post_json
from weeks import week_date, week_label

COLUMN_NAMES = {
    COL_WEEK: "Week",
    COL_RESEARCHER: "Researcher",
    COL_SUMMARY: "Summary",
    COL_BLOCKERS: "Blockers",
    COL_STATUS: "Status",
}
if COL_CONFIRMED:
    COLUMN_NAMES[COL_CONFIRMED] = "Confirmed"
if COL_LINK:
    COLUMN_NAMES[COL_LINK] = "Link to Work"
KIND = {COL_WEEK: COLUMN_TYPES["COL_WEEK"], COL_RESEARCHER: COLUMN_TYPES["COL_RESEARCHER"], COL_STATUS: COLUMN_TYPES["COL_STATUS"]}

NAMED = {name.strip().lower(): uid for uid, name in RESEARCHER_NAMES.items()}


def rich_text(text):
    return [{
        "type": "rich_text",
        "elements": [{"type": "rich_text_section", "elements": [{"type": "text", "text": text}]}],
    }]


def link_rich_text(text):
    elements = []
    for position, url in enumerate(text.split("\n")):
        if position:
            elements.append({"type": "text", "text": "\n"})
        elements.append({"type": "link", "url": url})
    return [{"type": "rich_text", "elements": [{"type": "rich_text_section", "elements": elements}]}]


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


def week_cell(week):
    return week_date(week, WEEK_DAY).isoformat() if KIND[COL_WEEK] == "date" else week_label(week)


def row_cells(report):
    cells = {
        COL_WEEK: week_cell(report["week"]),
        COL_SUMMARY: cell_text(decoded(report["summary"])),
        COL_BLOCKERS: cell_text(decoded(report["blockers"])),
        COL_STATUS: report["status"],
        COL_RESEARCHER: report["researcher"],
    }
    if COL_CONFIRMED:
        cells[COL_CONFIRMED] = bool(report["confirmed"])
    if COL_LINK:
        cells[COL_LINK] = cell_text(decoded(report["work_links"]))
    return cells


def item_cells(item):
    fields = {f["column_id"]: f for f in item["fields"] if f.get("column_id")}
    cells = {
        COL_WEEK: week_read(fields.get(COL_WEEK, {})),
        COL_SUMMARY: fields.get(COL_SUMMARY, {}).get("text"),
        COL_BLOCKERS: fields.get(COL_BLOCKERS, {}).get("text"),
        COL_STATUS: status_read(fields.get(COL_STATUS, {})),
        COL_RESEARCHER: researcher_read(fields.get(COL_RESEARCHER, {})),
    }
    if COL_CONFIRMED:
        cells[COL_CONFIRMED] = bool(fields.get(COL_CONFIRMED, {}).get("checkbox"))
    if COL_LINK:
        cells[COL_LINK] = fields.get(COL_LINK, {}).get("text")
    return cells


def week_read(field):
    if KIND[COL_WEEK] == "date":
        return (field.get("date") or [None])[0]
    return field.get("text")


def status_read(field):
    if KIND[COL_STATUS] == "select":
        return STATUS_LABELS.get((field.get("select") or [None])[0])
    text = (field.get("text") or "").strip()
    # wording stays as it is
    return next((label for label in STATUS_OPTIONS if label.lower() == text.lower()), text or None)


def researcher_read(field):
    if KIND[COL_RESEARCHER] == "user":
        return (field.get("user") or [None])[0]
    return NAMED.get((field.get("text") or "").strip().lower())


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
        return {"select": [STATUS_OPTIONS[value]]} if KIND[COL_STATUS] == "select" else {"rich_text": rich_text(value)}
    if column_id == COL_RESEARCHER:
        return {"user": [value]} if KIND[COL_RESEARCHER] == "user" else {"rich_text": rich_text(RESEARCHER_NAMES[value])}
    if column_id == COL_WEEK and KIND[COL_WEEK] == "date":
        return {"date": [value]}
    if column_id == COL_LINK and value != NOT_APPLICABLE:
        return {"rich_text": link_rich_text(value)}
    return {"rich_text": rich_text(value)}


def create_row(report):
    fields = [dict(column_id=cid, **cell_value(cid, val)) for cid, val in row_cells(report).items() if val is not None]
    result = post_json("slackLists.items.create", {"list_id": LIST_ID, "initial_fields": fields})
    return result["item"]["id"]


def update_cells(row_id, changes):
    cells = [dict(row_id=row_id, column_id=cid, **cell_value(cid, val)) for cid, val in changes.items()]
    post_json("slackLists.items.update", {"list_id": LIST_ID, "cells": cells})
