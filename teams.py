import hashlib
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# the app on a researcher's computer keeps its settings and data in their user folder
DATA_DIR = Path(os.environ.get("REPORT_BOT_DATA") or ROOT / "data")
# which llms and models a deployment offers, absent on a researcher's computer where anything goes
PROVIDER_LIMITS = DATA_DIR / "providers.json"
DEPLOYMENT_ENV = Path(os.environ["REPORT_BOT_DATA"]) / ".env" if os.environ.get("REPORT_BOT_DATA") else ROOT / ".env"
TEAMS_DIR = DATA_DIR / "teams"
INVITES_DIR = DATA_DIR / "invites"
SESSIONS_DIR = DATA_DIR / "sessions"
APP_KEYS_DIR = DATA_DIR / "app-keys"
# channels the bot was added to and by whom, from slack's join messages
JOINS = DATA_DIR / "joins.json"
# how far the search for joins from before events were on has got
BACKFILL = DATA_DIR / "backfill.json"
CACHE_DIR = DATA_DIR / "cache"
NAME = r"[a-z0-9][a-z0-9-]{0,31}"
INVITE_DAYS = 7
SESSION_DAYS = 182


def valid(name):
    return bool(re.fullmatch(NAME, name or ""))


def folder(name):
    if not valid(name):
        raise ValueError(f"team name {name!r} must be lowercase letters, digits and dashes")
    return TEAMS_DIR / name


def env_path(name):
    return folder(name) / "team.env"


def profile_path(name):
    # what the gateway allows a team running on its own computer to touch
    return folder(name) / "profile.json"


DEFAULT_REMINDER = "Don't forget to post your Week {week} report to the channel when you can :-)"


def read_env(path):
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value
    return values


def write_env(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".new")
    temp.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    os.replace(temp, path)


def paused(name):
    # stopped from the dashboard, settings kept, wherever the team runs
    return (folder(name) / "paused").is_file()


def set_paused(name, value):
    marker = folder(name) / "paused"
    if value:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
    else:
        marker.unlink(missing_ok=True)


def names():
    if not TEAMS_DIR.is_dir():
        return []
    return sorted(d.name for d in TEAMS_DIR.iterdir() if valid(d.name) and (d / "team.env").is_file())


def take_team_arg(argv):
    # sets the team before config is imported and removes the flag from the arguments
    if "--team" in argv:
        at = argv.index("--team")
        if at + 1 >= len(argv):
            raise SystemExit("--team needs a team name")
        os.environ["TEAM"] = argv[at + 1]
        del argv[at:at + 2]


APP_RUNNING_SECONDS = 180


def write_json(path, value):
    # swapped into place whole, so a request reading it at the same moment never sees half a file
    temp = path.with_name(path.name + f".{os.getpid()}.{threading.get_ident()}.new")
    temp.write_text(json.dumps(value), encoding="utf-8")
    for attempt in range(20):
        try:
            return os.replace(temp, path)
        except PermissionError:
            # windows refuses while another request has the file open for reading
            if attempt == 19:
                raise
            time.sleep(0.05)


def secret_path(folder, key):
    # only a hash of the key is kept on disk
    return folder / (hashlib.sha256(key.encode()).hexdigest() + ".json")


def create_invite(team, user):
    folder(team)
    token = secrets.token_urlsafe(24)
    INVITES_DIR.mkdir(parents=True, exist_ok=True)
    write_json(secret_path(INVITES_DIR, token), {
        "team": team, "user": user, "expires": time.time() + INVITE_DAYS * 86400,
    })
    return token


def claim_invite(token):
    # a used invite is kept, marked used, so its page can still say who it was for
    invite = invite_record(token)
    if not invite or invite.get("used") or invite["expires"] < time.time():
        return None
    invite["used"] = time.time()
    write_json(secret_path(INVITES_DIR, token), invite)
    return invite


def invite_record(token):
    if not token:
        return None
    path = secret_path(INVITES_DIR, token)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def mark_requested(token):
    invite = invite_record(token)
    invite["requested"] = time.time()
    write_json(secret_path(INVITES_DIR, token), invite)


def create_session(team, user):
    key = secrets.token_urlsafe(32)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    write_json(secret_path(SESSIONS_DIR, key), {
        "team": team, "user": user, "expires": time.time() + SESSION_DAYS * 86400,
    })
    return key


def find_session(key):
    path = secret_path(SESSIONS_DIR, key)
    if not key or not path.is_file():
        return None
    session = json.loads(path.read_text(encoding="utf-8"))
    if session["expires"] < time.time():
        path.unlink()
        return None
    # renewed at most once a day, so the page's many requests do not keep rewriting it
    if session["expires"] - time.time() < (SESSION_DAYS - 1) * 86400:
        session["expires"] = time.time() + SESSION_DAYS * 86400
        write_json(path, session)
    return session


def team_secrets(folder, team):
    for path in folder.glob("*.json") if folder.is_dir() else []:
        found = json.loads(path.read_text(encoding="utf-8"))
        if found["team"] == team:
            yield path, found


def revoke_sessions(team):
    revoked = 0
    for path, _ in list(team_secrets(SESSIONS_DIR, team)):
        path.unlink()
        revoked += 1
    return revoked


def create_app_key(team, user):
    # one key per team, a new one disconnects the app holding the old one
    for path, _ in list(team_secrets(APP_KEYS_DIR, team)):
        path.unlink()
    key = secrets.token_urlsafe(32)
    APP_KEYS_DIR.mkdir(parents=True, exist_ok=True)
    write_json(secret_path(APP_KEYS_DIR, key), {"team": team, "user": user, "seen": None})
    return key


def find_app_key(key):
    path = secret_path(APP_KEYS_DIR, key)
    if not key or not path.is_file():
        return None
    found = json.loads(path.read_text(encoding="utf-8"))
    found["seen"] = time.time()
    write_json(path, found)
    return found


def app_window(key, opened):
    # the app's settings window reports itself every few seconds while open, so a crashed app unlocks the page
    path = secret_path(APP_KEYS_DIR, key)
    found = json.loads(path.read_text(encoding="utf-8"))
    found["window_until"] = time.time() + 30 if opened else 0
    write_json(path, found)


def app_status(team):
    # a running app reaches the server at least every minute, so a quiet one has been quit
    for _, found in team_secrets(APP_KEYS_DIR, team):
        return {"seen": found["seen"], "window_open": found.get("window_until", 0) > time.time(),
                "running": bool(found["seen"]) and time.time() - found["seen"] < APP_RUNNING_SECONDS}
    return None
