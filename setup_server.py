import ast
import http.cookies
import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import slack_http
import teams

PAGE = teams.ROOT / "setup"
STATIC = {"app.js": "text/javascript; charset=utf-8", "style.css": "text/css; charset=utf-8", "invite.png": "image/png"}
NO_REPORT = "No Report"
COOKIE = "setup_session"
POST_SUBTYPES = (None, "file_share", "thread_broadcast")
EXAMPLE_BYTES = 100 * 1024 * 1024
DEADLINE_TZ = "America/New_York"
ROLES = {
    "COL_WEEK": ("Week", "text", "week"),
    "COL_RESEARCHER": ("Researcher", "user", "researcher"),
    "COL_SUMMARY": ("Summary", "text", "summar"),
    "COL_BLOCKERS": ("Blockers", "text", "block"),
    "COL_STATUS": ("Status", "select", "status"),
    "COL_CONFIRMED": ("Confirmed", "checkbox", "confirm"),
}

deployment = teams.read_env(teams.DEPLOYMENT_ENV)
users = {}
active = {}


class Problem(Exception):
    pass


def slack(method, params):
    return slack_http.form(deployment["SLACK_BOT_TOKEN"], method, params)


def lookup(uid):
    # names and photos come from a separate service so slack needs no users scope
    if uid not in users:
        users[uid] = {"id": uid, "name": uid, "photo": None}
        if deployment.get("USER_LOOKUP_URL"):
            try:
                with urllib.request.urlopen(f"{deployment['USER_LOOKUP_URL'].rstrip('/')}/{uid}", timeout=5) as resp:
                    found = json.load(resp)
                users[uid] = {"id": uid, "name": found.get("name") or uid, "photo": found.get("photo")}
            except (OSError, ValueError):
                pass
    return users[uid]


def week_zero(week_one_monday):
    try:
        monday = datetime.strptime(week_one_monday or "", "%Y-%m-%d")
    except ValueError:
        raise Problem("pick the monday of week 1 first")
    if monday.weekday() != 0:
        raise Problem("week 1 has to start on a monday")
    return (monday - timedelta(days=7)).replace(tzinfo=timezone.utc).timestamp()


def modules(function):
    # read without importing since importing a module pulls in a team config
    found = []
    for path in sorted((teams.ROOT / "processors").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(isinstance(node, ast.FunctionDef) and node.name == function for node in tree.body):
            found.append(f"processors.{path.stem}")
    return found


def person_post(message):
    # joins and other system messages carry a user id too, including app bot users without a bot_id
    return (message.get("subtype") in POST_SUBTYPES and not message.get("bot_id")
            and message.get("user") not in (None, "USLACKBOT", deployment["BOT_USER_ID"]))


def channel_check(link, week_one_monday):
    match = re.search(r"\b([CG][A-Z0-9]{8,})\b", link or "")
    if not match:
        raise Problem("that does not look like a channel link")
    channel = match.group(1)
    oldest = week_zero(week_one_monday)
    latest, cursor = {}, None
    try:
        while True:
            params = {"channel": channel, "oldest": oldest, "limit": 200}
            if cursor:
                params["cursor"] = cursor
            result = slack("conversations.history", params)
            for m in result["messages"]:
                if person_post(m):
                    latest[m["user"]] = max(latest.get(m["user"], 0.0), float(m["ts"]))
            cursor = result.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
    except slack_http.SlackError:
        raise Problem("the bot cannot read that channel yet, invite it with /invite first")
    # most recent posters first since they are the likeliest researchers
    return channel, [lookup(uid) for uid in sorted(latest, key=latest.get, reverse=True)]


def list_check(link):
    match = re.search(r"\b(F[A-Z0-9]{8,})\b", link or "")
    if not match:
        raise Problem("that does not look like a List link")
    list_id = match.group(1)
    try:
        info = slack("files.info", {"file": list_id})["file"]
    except slack_http.SlackError:
        raise Problem("the bot cannot open that List yet, share it with the bot first")
    schema = info.get("list_metadata", {}).get("schema")
    if not schema:
        raise Problem("that link is not a Slack List")
    roles = {}
    for key, (label, kind, word) in ROLES.items():
        candidates = [c for c in schema if c["type"] == kind]
        named = [c for c in candidates if word in c["name"].lower()]
        roles[key] = {
            "label": label,
            "candidates": [{"id": c["id"], "name": c["name"]} for c in candidates],
            "chosen": named[0]["id"] if len(named) == 1 else None,
        }
    choices = {c["id"]: {o["label"]: o["value"] for o in c.get("options", {}).get("choices", [])}
               for c in schema if c["type"] == "select"}
    return list_id, roles, choices


def save(session, body):
    channel, people = channel_check(body.get("channel"), body.get("week_one_monday"))
    list_id, roles, choices = list_check(body.get("list"))
    columns = body.get("columns") or {}
    for key, role in roles.items():
        if columns.get(key) not in {c["id"] for c in role["candidates"]}:
            raise Problem(f"pick the {role['label']} column")
    if len(set(columns[key] for key in roles)) != len(roles):
        raise Problem("each column can only be used once")
    statuses = choices[columns["COL_STATUS"]]
    if NO_REPORT not in statuses:
        raise Problem(f"the Status column needs a {NO_REPORT} option, the deadline writes it")
    poster_ids = {p["id"] for p in people}
    researchers = body.get("researchers") or []
    if not researchers:
        raise Problem("pick at least one researcher")
    if not set(researchers) <= poster_ids:
        raise Problem("researchers must be active users in the channel")
    try:
        deadline_time = datetime.strptime(body.get("deadline_time") or "", "%H:%M").strftime("%H:%M")
    except ValueError:
        raise Problem("pick the time the week closes")
    if not re.fullmatch(r"[a-z0-9_+'-]+", body.get("react_emoji") or ""):
        raise Problem("pick a reaction emoji")
    if body.get("classifier") not in modules("classify") or body.get("summarizer") not in modules("summarize"):
        raise Problem("pick a classifier and a summarizer from the lists")
    path = teams.env_path(session["team"])
    previous = teams.read_env(path) if path.is_file() else {}
    teams.write_env(path, {
        "SLACK_CHANNEL_ID": channel,
        "SLACK_LIST_ID": list_id,
        **{key: columns[key] for key in roles},
        "STATUS_OPTIONS": ",".join(f"{label}={value}" for label, value in statuses.items()),
        "ALLOWED_POSTERS": " ".join(researchers),
        "RESEARCHERS": " ".join(researchers),
        "DEBUG_DM_USER": session["user"],
        "REACT_ENABLED": "true" if body.get("react_enabled") else "false",
        "REACT_EMOJI": body["react_emoji"],
        # reactions start from now so a new team never reacts to old posts
        "REACT_FROM_TS": previous.get("REACT_FROM_TS") or f"{time.time():.6f}",
        "SEND_REMINDERS": "true" if body.get("send_reminders") else "false",
        "DEADLINE_TZ": DEADLINE_TZ,
        "DEADLINE_TIME": deadline_time,
        "WEEK_ONE_MONDAY": body["week_one_monday"],
        "CLASSIFIER": body["classifier"],
        "SUMMARIZER": body["summarizer"],
    })
    slack_http.post_json(deployment["SLACK_BOT_TOKEN"], "chat.postMessage", {
        "channel": deployment["OPERATOR_USER"],
        "text": f"*team {session['team']} {'updated' if previous else 'set up'}* by <@{session['user']}>,"
                f" channel <#{channel}>, the supervisor starts it within seconds",
    })


def examples_folder(session, uid=None):
    folder = teams.folder(session["team"]) / "examples"
    if uid is None:
        return folder
    if not re.fullmatch(r"[UW][A-Z0-9]{8,}", uid or ""):
        raise Problem("unknown researcher")
    return folder / uid


def examples(session):
    folder = examples_folder(session)
    if not folder.is_dir():
        return {}
    return {d.name: [{"name": f.name, "size": f.stat().st_size} for f in sorted(d.iterdir()) if f.is_file()]
            for d in sorted(folder.iterdir()) if d.is_dir()}


def add_example(session, key, uid, name, data):
    if uid not in active.get(key, ()):
        raise Problem("examples can only be added for active users in the channel")
    stem, suffix = os.path.splitext(os.path.basename(name or ""))
    stem = re.sub(r"[^A-Za-z0-9._ -]", "_", stem).strip(" .")[:80] or "report"
    suffix = re.sub(r"[^A-Za-z0-9.]", "", suffix)[:10].lower()
    folder = examples_folder(session, uid)
    folder.mkdir(parents=True, exist_ok=True)
    target, copy = folder / f"{stem}{suffix}", 1
    while target.exists():
        copy += 1
        target = folder / f"{stem} ({copy}){suffix}"
    target.write_bytes(data)


def remove_example(session, uid, name):
    target = examples_folder(session, uid) / os.path.basename(name or "")
    if target.is_file():
        target.unlink()


def state(session):
    path = teams.env_path(session["team"])
    return {
        "team": session["team"],
        "user": lookup(session["user"]),
        "values": teams.read_env(path) if path.is_file() else {},
        "classifiers": modules("classify"),
        "summarizers": modules("summarize"),
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def send(self, status, body, content_type="application/json", headers=()):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' https:;"
                         " script-src 'self' https://cdn.jsdelivr.net; connect-src 'self' https://cdn.jsdelivr.net;"
                         " style-src 'self' 'unsafe-inline'")
        self.send_header("Referrer-Policy", "no-referrer")
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def route(self):
        parts = self.path.split("?")[0].strip("/").split("/")
        return "/".join(parts[1:]) if parts[0] == "setup" else None

    def session(self):
        cookie = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
        key = cookie[COOKIE].value if COOKIE in cookie else ""
        return key, teams.find_session(key)

    def cookie(self, key):
        # lax rather than strict so the page still opens from a link clicked in slack
        return ("Set-Cookie", f"{COOKIE}={key}; Path=/setup; Max-Age={teams.SESSION_DAYS * 86400};"
                              " HttpOnly; Secure; SameSite=Lax")

    def gone(self):
        self.send(404, b"This setup link has expired or was already used. Ask for a new invite.", "text/plain; charset=utf-8")

    def do_GET(self):
        kind = self.route()
        if kind is None:
            return self.send(404, {"error": "not found"})
        if kind.startswith("static/") and kind[7:] in STATIC:
            return self.send(200, (PAGE / kind[7:]).read_bytes(), STATIC[kind[7:]])
        key, session = self.session()
        if kind and not kind.startswith("api/"):
            # an invite works once and belongs to the first browser that opens it
            invite = teams.claim_invite(kind)
            if invite:
                key, session = teams.create_session(invite["team"], invite["user"]), invite
            if not session:
                return self.gone()
            return self.send(303, b"", "text/plain", [("Location", "/setup/"), self.cookie(key)])
        if not session:
            return self.gone() if not kind else self.send(401, {"error": "this setup session has ended, ask for a new invite"})
        if not kind:
            return self.send(200, (PAGE / "index.html").read_bytes(), "text/html; charset=utf-8", [self.cookie(key)])
        if kind == "api/state":
            return self.send(200, state(session))
        if kind == "api/examples":
            return self.send(200, examples(session))
        self.send(404, {"error": "not found"})

    def do_POST(self):
        kind = self.route() or ""
        key, session = self.session()
        if not session:
            return self.send(401, {"error": "this setup session has ended, ask for a new invite"})
        length = int(self.headers.get("Content-Length") or 0)
        if kind.startswith("api/examples/"):
            if length > EXAMPLE_BYTES:
                return self.send(413, {"error": "examples can be at most 100 MB"})
            name = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("name", [""])[0]
            try:
                add_example(session, key, kind.split("/")[2], name, self.rfile.read(length))
            except Problem as problem:
                return self.send(400, {"error": str(problem)})
            return self.send(200, examples(session))
        if length > 65536:
            return self.send(413, {"error": "too large"})
        body = json.loads(self.rfile.read(length) or b"{}")
        try:
            if kind == "api/channel":
                channel, people = channel_check(body.get("link"), body.get("week_one_monday"))
                active[key] = {p["id"] for p in people}
                return self.send(200, {"channel": channel, "people": people})
            if kind == "api/list":
                list_id, roles, choices = list_check(body.get("link"))
                return self.send(200, {"list": list_id, "roles": roles, "statuses": {k: list(v) for k, v in choices.items()}})
            if kind == "api/save":
                save(session, body)
                return self.send(200, {"team": session["team"]})
        except Problem as problem:
            return self.send(400, {"error": str(problem)})
        self.send(404, {"error": "not found"})

    def do_DELETE(self):
        parts = (self.route() or "").split("/")
        key, session = self.session()
        if not session:
            return self.send(401, {"error": "this setup session has ended, ask for a new invite"})
        if len(parts) != 4 or parts[:2] != ["api", "examples"]:
            return self.send(404, {"error": "not found"})
        try:
            remove_example(session, parts[2], urllib.parse.unquote(parts[3]))
        except Problem as problem:
            return self.send(400, {"error": str(problem)})
        self.send(200, examples(session))


if __name__ == "__main__":
    port = int(deployment["SETUP_PORT"])
    print(f"setup pages on 127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
