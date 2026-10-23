import json
import sqlite3
import time
from contextlib import closing
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import teams

RUNNING_FILE = "running.json"
RECENT_LOG = 200
WHAT = {
    "report_filed": "Report filed",
    "resubmitted": "Report replaced by a newer one",
    "adopted": "Row adopted from the List",
    "edited_in_slack": "Edit pulled in from the List",
    "marked_no_report": "Marked No Report at the deadline",
    "confirmed_changed": "Confirmed changed in the List",
    "row_deleted_in_slack": "Row deleted in the List",
    "row_linked": "Row created in the List",
    "deadline_enforced": "Week closed",
    "reacted": "Reacted to the post",
}


def mark_started(team):
    teams.write_json(teams.folder(team) / RUNNING_FILE, {"started": time.time()})


def mark_stopped(team):
    (teams.folder(team) / RUNNING_FILE).unlink(missing_ok=True)


def started(team):
    path = teams.folder(team) / RUNNING_FILE
    return json.loads(path.read_text(encoding="utf-8"))["started"] if path.is_file() else None


def database(team):
    # read only, the bot owns the database and may be writing to it
    path = teams.folder(team) / "state.db"
    if not path.is_file():
        return None
    db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


def week_of(day, week_one):
    return (day - week_one).days // 7 + 1


def summary(team, names):
    env = teams.read_env(teams.env_path(team))
    tz = ZoneInfo(env["DEADLINE_TZ"])
    week_one = date.fromisoformat(env["WEEK_ONE_MONDAY"])
    due_time = datetime.strptime(env["DEADLINE_TIME"], "%H:%M").time()
    now = datetime.now(tz)
    week = week_of(now.date(), week_one)
    monday = week_one + timedelta(weeks=week - 1)
    # the friday deadline of this week, or next week's once this one has passed
    due = datetime.combine(monday + timedelta(days=4), due_time, tz)
    if now >= due:
        due += timedelta(weeks=1)
    researchers = env["RESEARCHERS"].split()
    found = {"week": week, "monday": monday.isoformat(), "due": due.isoformat(), "started": started(team),
             "researchers": len(researchers), "filed_this_week": 0, "latest": None}
    db = database(team)
    if db is None:
        return found
    with closing(db):
        filed = db.execute("select count(*) from reports where week = ? and (status is null or status != 'No Report')", (week,)).fetchone()[0]
        latest = db.execute("select * from history order by id desc limit 1").fetchone()
    found["filed_this_week"] = filed
    found["latest"] = entry(latest, names) if latest else None
    return found


def entry(row, names):
    who = names(row["researcher"]) if row["researcher"] else None
    return {"at": row["at"], "what": WHAT.get(row["action"], row["action"].replace("_", " ").capitalize()),
            "who": who, "week": row["week"], "cause": row["cause"]}


def log(team, names):
    db = database(team)
    if db is None:
        return []
    with closing(db):
        rows = db.execute("select * from history order by id desc limit ?", (RECENT_LOG,)).fetchall()
    return [entry(row, names) for row in rows]


def tables(team):
    db = database(team)
    if db is None:
        return []
    with closing(db):
        listed = [r["name"] for r in db.execute("select name from sqlite_master where type = 'table' and name not like 'sqlite_%' order by name")]
        found = []
        for name in listed:
            cursor = db.execute(f'select * from "{name}"')
            found.append({"name": name, "columns": [c[0] for c in cursor.description], "rows": [list(r) for r in cursor.fetchall()]})
    return found
