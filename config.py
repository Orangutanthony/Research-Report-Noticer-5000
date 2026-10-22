import os
from datetime import datetime
from zoneinfo import ZoneInfo

import teams


def chosen_team():
    named = os.environ.get("TEAM")
    if named:
        if named not in teams.names():
            raise SystemExit(f"no team {named!r}, expected {teams.env_path(named)}")
        return named
    found = teams.names()
    if len(found) == 1:
        return found[0]
    if not found:
        raise SystemExit(f"no teams, add one as {teams.TEAMS_DIR}/<name>/team.env")
    raise SystemExit(f"several teams exist, pick one with TEAM=<name>: {', '.join(found)}")


TEAM = chosen_team()
env = {**teams.read_env(teams.DEPLOYMENT_ENV), **teams.read_env(teams.env_path(TEAM))}

TOKEN = env["SLACK_BOT_TOKEN"]
BOT_USER_ID = env["BOT_USER_ID"]
CHANNEL = env["SLACK_CHANNEL_ID"]
LIST_ID = env["SLACK_LIST_ID"]
COL_WEEK = env["COL_WEEK"]
COL_RESEARCHER = env["COL_RESEARCHER"]
COL_SUMMARY = env["COL_SUMMARY"]
COL_BLOCKERS = env["COL_BLOCKERS"]
COL_STATUS = env["COL_STATUS"]
COL_CONFIRMED = env["COL_CONFIRMED"]
STATUS_OPTIONS = dict(pair.split("=", 1) for pair in env["STATUS_OPTIONS"].split(","))
STATUS_LABELS = {v: k for k, v in STATUS_OPTIONS.items()}
ALLOWED_POSTERS = set(env["ALLOWED_POSTERS"].split())
RESEARCHERS = env["RESEARCHERS"].split()
DEBUG_DM_USER = env["DEBUG_DM_USER"]
REACT_ENABLED = env["REACT_ENABLED"].lower() == "true"
REACT_EMOJI = env["REACT_EMOJI"]
REACT_FROM_TS = float(env["REACT_FROM_TS"])
SEND_REMINDERS = env["SEND_REMINDERS"].lower() == "true"
DEADLINE_TZ = ZoneInfo(env["DEADLINE_TZ"])
DEADLINE_TIME = datetime.strptime(env["DEADLINE_TIME"], "%H:%M").time()
WEEK_ONE_MONDAY = datetime.strptime(env["WEEK_ONE_MONDAY"], "%Y-%m-%d").date()
CLASSIFIER = env["CLASSIFIER"]
SUMMARIZER = env.get("SUMMARIZER") or "processors.manual_summarizer"
STATE_DB = teams.folder(TEAM) / "state.db"

# status written on deadline for naughty researchers
NO_REPORT = "No Report"

# checked and nothing applies
NOT_APPLICABLE = "N/A"
