import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEPLOYMENT_ENV = ROOT / ".env"
TEAMS_DIR = ROOT / "teams"
INVITES_DIR = ROOT / "invites"
SESSIONS_DIR = ROOT / "sessions"
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


def secret_path(folder, key):
    # only a hash of the key is kept on disk
    return folder / (hashlib.sha256(key.encode()).hexdigest() + ".json")


def create_invite(team, user):
    folder(team)
    token = secrets.token_urlsafe(24)
    INVITES_DIR.mkdir(exist_ok=True)
    secret_path(INVITES_DIR, token).write_text(json.dumps({
        "team": team, "user": user, "expires": time.time() + INVITE_DAYS * 86400,
    }), encoding="utf-8")
    return token


def claim_invite(token):
    path = secret_path(INVITES_DIR, token)
    if not path.is_file():
        return None
    invite = json.loads(path.read_text(encoding="utf-8"))
    path.unlink()
    return invite if invite["expires"] >= time.time() else None


def create_session(team, user):
    key = secrets.token_urlsafe(32)
    SESSIONS_DIR.mkdir(exist_ok=True)
    secret_path(SESSIONS_DIR, key).write_text(json.dumps({
        "team": team, "user": user, "expires": time.time() + SESSION_DAYS * 86400,
    }), encoding="utf-8")
    return key


def find_session(key):
    path = secret_path(SESSIONS_DIR, key)
    if not key or not path.is_file():
        return None
    session = json.loads(path.read_text(encoding="utf-8"))
    if session["expires"] < time.time():
        path.unlink()
        return None
    session["expires"] = time.time() + SESSION_DAYS * 86400
    path.write_text(json.dumps(session), encoding="utf-8")
    return session


def revoke_sessions(team):
    revoked = 0
    for path in SESSIONS_DIR.glob("*.json") if SESSIONS_DIR.is_dir() else []:
        if json.loads(path.read_text(encoding="utf-8"))["team"] == team:
            path.unlink()
            revoked += 1
    return revoked
