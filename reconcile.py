import store
from config import BOT_USER_ID, COL_BLOCKERS, COL_CONFIRMED, COL_LINK, COL_RESEARCHER, COL_STATUS, COL_SUMMARY, COL_WEEK
from slack_api import notify
from slack_list import (
    COLUMN_NAMES, cell_lines, create_row, decoded, has_content, item_cells, list_items, row_cells, update_cells,
)
from weeks import parse_week


def column_names(columns):
    return ", ".join(COLUMN_NAMES.get(c, c) for c in columns)


def adopt(item):
    cells = item_cells(item)
    researcher, week = cells[COL_RESEARCHER], parse_week(cells[COL_WEEK])
    if not researcher or not week:
        return None
    store.save_report(
        "adopted", researcher, week,
        cells[COL_STATUS],
        cell_lines(cells[COL_SUMMARY]),
        cell_lines(cells[COL_BLOCKERS]),
        cell_lines(cells.get(COL_LINK)),
        cause=f"row {item['id']}", row_id=item["id"],
    )
    return f"*adopted row* `{item['id']}`: <@{researcher}> week {week}, status {cells[COL_STATUS] or 'unset'}"


def reconcile(thread_ts=None):
    notes = []
    items = {item["id"]: item for item in list_items()}
    known = {row["row_id"] for row in store.all_reports() if row["row_id"]}
    for row_id, item in items.items():
        if row_id not in known:
            note = adopt(item)
            if note:
                notes.append(note)
    for report in store.all_reports():
        row_id = report["row_id"]
        who = f"<@{report['researcher']}> week {report['week']}"
        if row_id and row_id not in items:
            if not report["detached"]:
                store.mark_detached(report["researcher"], report["week"])
                notes.append(f"*row deleted in Slack*: `{row_id}`, {who}")
            continue
        if report["detached"]:
            continue
        wanted = row_cells(report)
        if not row_id:
            new_id = create_row(report)
            store.link_row(report["researcher"], report["week"], new_id)
            store.mark_clean(report["researcher"], report["week"])
            notes.append(f"*created row* `{new_id}`: {who}")
            continue
        current = item_cells(items[row_id])
        # confirmed belongs to whoever ticks it so it is only ever read back here
        if COL_CONFIRMED:
            if current.get(COL_CONFIRMED) != bool(report["confirmed"]):
                store.set_confirmed(report["researcher"], report["week"], current.get(COL_CONFIRMED))
                notes.append(f"*Confirmed {'ticked' if current.get(COL_CONFIRMED) else 'cleared'}* on row `{row_id}`, {who}")
            wanted.pop(COL_CONFIRMED)
        # unset in the database and blank in slack are the same thing
        differing = {cid: val for cid, val in wanted.items()
                     if current.get(cid) != val and not (val is None and not (current.get(cid) or "").strip())}
        if not differing:
            store.mark_clean(report["researcher"], report["week"])
            continue
        edited_by = items[row_id].get("updated_by")
        by_person = edited_by and edited_by != BOT_USER_ID
        # week is derived here so it is always pushed and a person editing it cannot win
        if COL_WEEK in differing:
            update_cells(row_id, {COL_WEEK: wanted[COL_WEEK]})
            notes.append(f"*rewrote Week* on row `{row_id}` to {wanted[COL_WEEK]}, {who}")
            differing.pop(COL_WEEK)
            if not differing:
                store.mark_clean(report["researcher"], report["week"])
                continue
        if by_person:
            # text a person wrote wins but a blank cell is unset rather than protected
            written = [c for c in differing if has_content(current.get(c))]
            if written:
                store.save_report(
                    "edited_in_slack", report["researcher"], report["week"],
                    current[COL_STATUS] if COL_STATUS in written else report["status"],
                    cell_lines(current[COL_SUMMARY]) if COL_SUMMARY in written else decoded(report["summary"]),
                    cell_lines(current[COL_BLOCKERS]) if COL_BLOCKERS in written else decoded(report["blockers"]),
                    cell_lines(current[COL_LINK]) if COL_LINK in written else decoded(report["work_links"]),
                    cause=f"edited by {edited_by}", row_id=row_id,
                )
                notes.append(f"*manual edit pulled into the database*: <@{edited_by}> changed"
                             f" {column_names(written)} on row `{row_id}`, {who}")
            differing = {c: v for c, v in differing.items() if c not in written}
        # clearing a cell through the api is unverified so unset values are never pushed
        stale = [c for c, v in differing.items() if v is None]
        push = {c: v for c, v in differing.items() if v is not None}
        if push:
            update_cells(row_id, push)
            action = "filled blank cells" if by_person else "pushed to Slack"
            notes.append(f"*{action}*: {column_names(push)} on row `{row_id}`, {who}")
        if stale and report["dirty"]:
            notes.append(f"*left in place*: {column_names(stale)} on row `{row_id}` are unset in the database"
                         f" but still hold older text in Slack, {who}")
        store.mark_clean(report["researcher"], report["week"])
    for note in notes:
        notify(note, thread_ts)
    return notes
