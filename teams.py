import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEPLOYMENT_ENV = ROOT / ".env"
TEAMS_DIR = ROOT / "teams"
NAME = r"[a-z0-9][a-z0-9-]{0,31}"


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
