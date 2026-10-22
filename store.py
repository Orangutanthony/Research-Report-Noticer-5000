import json
import sqlite3
from datetime import datetime, timezone

from config import STATE_DB

db = sqlite3.connect(STATE_DB, isolation_level=None, check_same_thread=False)
db.row_factory = sqlite3.Row
db.executescript("""
create table if not exists cursor (id integer primary key check (id = 1), ts text not null);
create table if not exists history (
    id integer primary key autoincrement,
    at text not null,
    action text not null,
    researcher text,
    week integer,
    payload text not null,
    cause text
);
create table if not exists reports (
    researcher text not null,
    week integer not null,
    status text,
    summary text,
    blockers text,
    source_file text,
    source_ts text,
    row_id text,
    dirty integer not null default 1,
    detached integer not null default 0,
    confirmed integer not null default 0,
    updated_at text,
    primary key (researcher, week)
);
create table if not exists deadlines (week integer primary key, at text not null);
""")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record(action, payload, researcher=None, week=None, cause=None):
    db.execute(
        "insert into history (at, action, researcher, week, payload, cause) values (?, ?, ?, ?, ?, ?)",
        (now(), action, researcher, week, json.dumps(payload, sort_keys=True), cause),
    )


def encoded(value):
    return None if value is None else json.dumps(value)


def get_cursor():
    row = db.execute("select ts from cursor").fetchone()
    return row["ts"] if row else None


def set_cursor(ts):
    db.execute("insert into cursor (id, ts) values (1, ?) on conflict (id) do update set ts = excluded.ts", (ts,))


def get_report(researcher, week):
    return db.execute("select * from reports where researcher = ? and week = ?", (researcher, week)).fetchone()


def all_reports():
    return db.execute("select * from reports order by week, researcher").fetchall()


def save_report(action, researcher, week, status, summary, blockers, source_file=None, source_ts=None, cause=None, row_id=None):
    payload = {
        "status": status,
        "summary": summary,
        "blockers": blockers,
        "source_file": source_file,
        "source_ts": source_ts,
        "row_id": row_id,
    }
    existing = get_report(researcher, week)
    # a row the user deleted is not reused and new content gets a fresh row
    detached = existing and existing["detached"]
    keep_row = None if detached else (row_id or (existing["row_id"] if existing else None))
    db.execute(
        """insert into reports (researcher, week, status, summary, blockers, source_file, source_ts, row_id, dirty, updated_at)
           values (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
           on conflict (researcher, week) do update set
             status=excluded.status, summary=excluded.summary, blockers=excluded.blockers,
             source_file=coalesce(excluded.source_file, reports.source_file),
             source_ts=coalesce(excluded.source_ts, reports.source_ts),
             row_id=excluded.row_id, dirty=1, detached=0, updated_at=excluded.updated_at""",
        (researcher, week, status, encoded(summary), encoded(blockers), source_file, source_ts, keep_row, now()),
    )
    record(action, payload, researcher, week, cause)


def set_confirmed(researcher, week, value):
    db.execute("update reports set confirmed = ? where researcher = ? and week = ?", (int(value), researcher, week))
    record("confirmed_changed", {"confirmed": bool(value)}, researcher, week)


def mark_detached(researcher, week):
    db.execute("update reports set detached = 1, dirty = 0 where researcher = ? and week = ?", (researcher, week))
    record("row_deleted_in_slack", {}, researcher, week)


def link_row(researcher, week, row_id):
    db.execute("update reports set row_id = ? where researcher = ? and week = ?", (row_id, researcher, week))
    record("row_linked", {"row_id": row_id}, researcher, week)


def mark_clean(researcher, week):
    db.execute("update reports set dirty = 0 where researcher = ? and week = ?", (researcher, week))


def deadline_enforced(week):
    return db.execute("select 1 from deadlines where week = ?", (week,)).fetchone() is not None


def mark_deadline_enforced(week):
    db.execute("insert or ignore into deadlines (week, at) values (?, ?)", (week, now()))
    record("deadline_enforced", {"week": week}, week=week)
