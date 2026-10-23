import ast
import hashlib
import hmac
import http.cookies
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler

import columns
import runners
import slack_http
import stats
import teams
from guard import Guard, Refused

PAGE = teams.ROOT / "setup"
STATIC = {
    "app.js": "text/javascript; charset=utf-8", "connect.js": "text/javascript; charset=utf-8",
    "style.css": "text/css; charset=utf-8", "invite.png": "image/png",
    "server.svg": "image/svg+xml", "computer.svg": "image/svg+xml", "noticer.png": "image/png", "favicon.png": "image/png",
    "gone.js": "text/javascript; charset=utf-8",
}
NO_REPORT = "No Report"
# the week finder's default, read here without importing it since that pulls in a team config
DEFAULT_WEEK_PATTERN = r"(?i)\bweek\s*(\d+)"
COOKIE = "setup_session"
POST_SUBTYPES = (None, "file_share", "thread_broadcast")
EXAMPLE_BYTES = 100 * 1024 * 1024
RULES_BYTES = 1024 * 1024
DEADLINE_TZ = "America/New_York"
DEFAULT_STATUSES = ("On Track", "Blocked", "Unclear", NO_REPORT)
MANUAL_LINKS = "processors.manual_links"
PREVIEW_ROWS = 3
SAMPLE_ROWS = 50
FRIDAY = 4
GITHUB_NAME = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})"
GITHUB_MEMBERS_SECONDS = 300
LIST_PAGES = 5
GITHUB_MEMBER_PAGES = 5
github_members_cache = {}

deployment = {}
# channel names, learned from files shared in them since reading a private channel's name needs permissions
channel_names = {}
# when a channel was last looked at, so the page's polling does not keep asking Slack
name_checked = {}
NAME_RECHECK_SECONDS = 60
BACKFILL_PAUSE = 1.5
# the backfill and incoming events both change the joins record
joins_lock = threading.Lock()
# channels the bot was removed from, found by reading because of permissions
gone = set()
USERS_FILE = teams.CACHE_DIR / "users.json"
people = {"mtime": None, "known": {}}
workspace = {}
active = {}
# set by the app on a researcher's computer, turns an app key into a link to its own setup page
on_connect = None
# set by the app on a researcher's computer, told when its settings window opens and closes
on_window = None


class Problem(Exception):
    pass


def reload():
    deployment.clear()
    if teams.DEPLOYMENT_ENV.is_file():
        deployment.update(teams.read_env(teams.DEPLOYMENT_ENV))
    workspace.clear()


def local():
    # served by the app on a researcher's computer rather than by the server
    return bool(deployment.get("GATEWAY_URL"))


def slack(method, params):
    return slack_http.form(deployment["SLACK_BOT_TOKEN"], method, params)


def gateway(path, body=None):
    request = urllib.request.Request(
        f"{deployment['GATEWAY_URL'].rstrip('/')}/setup/gateway/{path}",
        json.dumps(body).encode() if body is not None else None,
        {"Authorization": f"Bearer {deployment['GATEWAY_KEY']}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as error:
        try:
            raise Problem(json.load(error)["error"])
        except (ValueError, KeyError):
            raise Problem(f"the report bot server answered {error.code}")
    except OSError:
        raise Problem("cannot reach the report bot server, check your internet connection")


def lookup(uid):
    # this whole thing is a placeholder until I can add groups:read to the bot
    # until then, assemble a .json of users by name/avatar using the dev console or whatever
    mtime = USERS_FILE.stat().st_mtime if USERS_FILE.is_file() else None
    if mtime != people["mtime"]:
        people["known"] = json.loads(USERS_FILE.read_text(encoding="utf-8")) if mtime else {}
        people["mtime"] = mtime
    found = people["known"].get(uid) or {}
    return {"id": uid, "name": found.get("name") or None, "photo": found.get("photo")}


def workspace_url():
    if "url" not in workspace:
        try:
            workspace["url"] = slack("auth.test", {})["url"]
        except (OSError, slack_http.SlackError):
            return None
    return workspace["url"]


def hello():
    if "hello" not in workspace:
        workspace["hello"] = gateway("hello")
    return workspace["hello"]


def week_zero(week_one_monday):
    try:
        monday = datetime.strptime(week_one_monday or "", "%Y-%m-%d")
    except ValueError:
        raise Problem("pick the monday of week 1 first")
    if monday.weekday() != 0:
        raise Problem("week 1 has to start on a monday")
    return (monday - timedelta(days=7)).replace(tzinfo=timezone.utc).timestamp()


def constant(tree, name):
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            try:
                return ast.literal_eval(node.value)
            except ValueError:
                return None
    return None


def modules(function):
    # read without importing since importing a module pulls in a team config
    found = []
    for path in sorted((teams.ROOT / "processors").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(isinstance(node, ast.FunctionDef) and node.name == function for node in tree.body):
            providers = constant(tree, "PROVIDERS")
            limits = provider_limits()
            if providers and limits is not None:
                providers = {k: v for k, v in providers.items() if k in limits}
            found.append({
                "name": f"processors.{path.stem}",
                "description": constant(tree, "DESCRIPTION") or path.stem,
                "examples": constant(tree, "USES_EXAMPLES") is True,
                "rules": constant(tree, "USES_RULES") is True,
                "providers": providers,
                "describes": constant(tree, "DESCRIBES"),
                "github": constant(tree, "USES_GITHUB") is True,
                "order": constant(tree, "ORDER"),
            })
    return sorted(found, key=lambda m: (m["order"] is None, m["order"] or 0))


def person_post(message):
    # joins and other system messages carry a user id too, including app bot users without a bot_id
    return (message.get("subtype") in POST_SUBTYPES and not message.get("bot_id")
            and message.get("user") not in (None, "USLACKBOT", deployment["BOT_USER_ID"]))


def channel_check(link, week_one_monday):
    if local():
        found = gateway("check/channel", {"link": link, "week_one_monday": week_one_monday})
        if found.get("name"):
            channel_names[found["channel"]] = found["name"]
        return found["channel"], found["people"]
    match = re.search(r"\b([CG][A-Z0-9]{8,})\b", link or "")
    if not match:
        raise Problem("that does not look like a channel link")
    channel = match.group(1)
    oldest = week_zero(week_one_monday)
    latest, cursor, shared = {}, None, None
    try:
        while True:
            params = {"channel": channel, "oldest": oldest, "limit": 200}
            if cursor:
                params["cursor"] = cursor
            result = slack("conversations.history", params)
            for m in result["messages"]:
                if person_post(m):
                    latest[m["user"]] = max(latest.get(m["user"], 0.0), float(m["ts"]))
                shared = shared or next((f["id"] for f in m.get("files", []) if f.get("id")), None)
            cursor = result.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
    except slack_http.SlackError:
        raise Problem("the bot cannot read that channel yet, invite it with /invite first")
    if shared and channel not in channel_names:
        learn_channel_name(channel, shared)
    # most recent posters first since they are the likeliest researchers
    return channel, [lookup(uid) for uid in sorted(latest, key=latest.get, reverse=True)]


def learn_channel_name(channel, file_id):
    try:
        shares = slack("files.info", {"file": file_id})["file"].get("shares", {})
    except slack_http.SlackError:
        return
    for place in ("private", "public"):
        for share in shares.get(place, {}).get(channel, []):
            if share.get("channel_name"):
                channel_names[channel] = share["channel_name"]
                return


def sample_text(field, column):
    # a cell as a person sees it, for the preview
    kind = column["type"]
    if kind == "checkbox":
        return "\u2713" if field.get("checkbox") else ""
    if kind == "user":
        return ", ".join(lookup(uid)["name"] or uid for uid in field.get("user") or [])
    if kind == "select":
        labels = {o["value"]: o["label"] for o in column.get("options", {}).get("choices", [])}
        return ", ".join(labels.get(v, v) for v in field.get("select") or [])
    if kind == "date":
        return ", ".join(field.get("date") or [])
    return (field.get("text") or "").strip()


def usual_weekday(rows, column_id):
    # a date column keeps the weekday its rows already use, fridays when it has none
    days = []
    for fields in rows:
        for value in fields.get(column_id, {}).get("date") or []:
            try:
                days.append(datetime.strptime(value, "%Y-%m-%d").weekday())
            except ValueError:
                pass
    return max(set(days), key=days.count) if days else FRIDAY


def list_check(link):
    if local():
        return gateway("check/list", {"link": link})
    match = re.search(r"\b(F[A-Z0-9]{8,})\b", link or "")
    if not match:
        raise Problem("that does not look like a List link")
    list_id = match.group(1)
    try:
        info = slack("files.info", {"file": list_id})["file"]
        items = slack("slackLists.items.list", {"list_id": list_id, "limit": SAMPLE_ROWS})["items"]
    except slack_http.SlackError:
        raise Problem("the bot cannot open that List yet, share it with the bot first")
    schema = info.get("list_metadata", {}).get("schema")
    if not schema:
        raise Problem("that link is not a Slack List")
    rows = [{f["column_id"]: f for f in item["fields"] if f.get("column_id")} for item in items]
    found = []
    for column in schema:
        entry = {"id": column["id"], "name": column["name"], "type": column["type"]}
        if column["type"] == "select":
            entry["options"] = [o["label"] for o in column.get("options", {}).get("choices", [])]
            entry["values"] = {o["label"]: o["value"] for o in column.get("options", {}).get("choices", [])}
        if column["type"] == "date":
            entry["weekday"] = usual_weekday(rows, column["id"])
        found.append(entry)
    by_id = {c["id"]: c for c in schema}
    return {
        "list": list_id,
        "name": info.get("title") or info.get("name") or list_id,
        "columns": found,
        "rows": [[sample_text(fields.get(c["id"], {}), by_id[c["id"]]) for c in found] for fields in rows[:PREVIEW_ROWS]],
        "roles": {key: {"label": r["label"], "optional": r["optional"], "types": list(r["types"])} for key, r in columns.ROLES.items()},
        "chosen": columns.match(schema),
    }


def column_settings(found, chosen, researchers):
    # what the bot needs to write each role into the column picked for it
    schema = {c["id"]: c for c in found["columns"]}
    types = {}
    for key, role in columns.ROLES.items():
        column_id = chosen.get(key)
        if not column_id:
            if role["optional"]:
                continue
            raise Problem(f"pick a column for {role['label']}")
        if column_id not in schema:
            raise Problem(f"the column picked for {role['label']} is not in the List")
        kind = schema[column_id]["type"]
        if kind not in role["types"]:
            raise Problem(f"{role['label']} cannot go in a {kind} column")
        types[key] = kind
    picked = [chosen[key] for key in types]
    if len(set(picked)) != len(picked):
        raise Problem("each column can only be used once")
    settings = {key: chosen[key] for key in types}
    status = schema[chosen["COL_STATUS"]]
    if types["COL_STATUS"] == "select":
        if NO_REPORT not in status["values"]:
            raise Problem(f"the Status column needs a {NO_REPORT} option, the deadline writes it")
        statuses = status["values"]
    else:
        statuses = {label: label for label in DEFAULT_STATUSES}
    settings["STATUS_OPTIONS"] = ",".join(f"{label}={value}" for label, value in statuses.items())
    settings["COLUMN_TYPES"] = json.dumps(types)
    if types["COL_WEEK"] == "date":
        settings["WEEK_DAY"] = str(schema[chosen["COL_WEEK"]]["weekday"])
    if types["COL_RESEARCHER"] == "text":
        names = {uid: lookup(uid)["name"] for uid in researchers}
        unnamed = [uid for uid, name in names.items() if not name]
        if unnamed:
            raise Problem("the Researcher column holds names, but some researchers have no known name, use a person column instead")
        settings["RESEARCHER_NAMES"] = json.dumps(names)
    return settings


def researchers_check(people, researchers):
    if not researchers:
        raise Problem("pick at least one researcher")
    if not set(researchers) <= {p["id"] for p in people}:
        raise Problem("researchers must be active users in the channel")


def save(session, body):
    team = session["team"]
    # saving here moves a team the app was running back to this server, once the app is quit
    moving_here = not local() and teams.profile_path(team).is_file()
    if moving_here and not teams.paused(team) and (teams.app_status(team) or {}).get("running"):
        raise Problem("the bot is running in the local app, stop it there first")
    channel, people = channel_check(body.get("channel"), body.get("week_one_monday"))
    found = list_check(body.get("list"))
    researchers = body.get("researchers") or []
    researchers_check(people, researchers)
    list_settings = column_settings(found, body.get("columns") or {}, researchers)
    try:
        deadline_time = datetime.strptime(body.get("deadline_time") or "", "%H:%M").strftime("%H:%M")
    except ValueError:
        raise Problem("pick the time the week closes")
    if not re.fullmatch(r"[a-z0-9_+'-]+", body.get("react_emoji") or ""):
        raise Problem("pick a reaction emoji")
    # without a Link to Work column there is nothing to find links for
    linker_name = body.get("linker") if (body.get("columns") or {}).get("COL_LINK") else MANUAL_LINKS
    if (body.get("classifier") not in [m["name"] for m in modules("classify")]
            or body.get("summarizer") not in [m["name"] for m in modules("summarize")]
            or linker_name not in [m["name"] for m in modules("find_links")]):
        raise Problem("pick a classifier, a summarizer and a way to find work links from the lists")
    classifier = next(m for m in modules("classify") if m["name"] == body["classifier"])
    if classifier["rules"] and not rules(session):
        raise Problem("upload your regex rules first")
    summarizer = next(m for m in modules("summarize") if m["name"] == body["summarizer"])
    linker = next(m for m in modules("find_links") if m["name"] == linker_name)
    provider = {
        **llm_settings(classifier, "LLM", body.get("llm_provider"), body.get("llm_model")),
        **llm_settings(summarizer, "SUMMARY", body.get("summary_provider"), body.get("summary_model")),
        **llm_settings(linker, "LINKS", body.get("links_provider"), body.get("links_model")),
        **(github_settings(body.get("github_repo"), body.get("github_handles"), researchers) if linker["github"] else {}),
        "WEEK_PATTERN": week_pattern(body.get("week_pattern")),
    }
    if "local" in (provider.get("LLM_PROVIDER"), provider.get("SUMMARY_PROVIDER"), provider.get("LINKS_PROVIDER")):
        provider.update(local_model_settings(body))
    path = teams.env_path(team)
    previous = teams.read_env(path) if path.is_file() else {}
    values = {
        "SLACK_CHANNEL_ID": channel,
        "SLACK_LIST_ID": found["list"],
        **list_settings,
        "ALLOWED_POSTERS": " ".join(researchers),
        "RESEARCHERS": " ".join(researchers),
        "DEBUG_DM_USER": session["user"],
        "REACT_ENABLED": "true" if body.get("react_enabled") else "false",
        "REACT_EMOJI": body["react_emoji"],
        # reactions start from now so a new team never reacts to old posts
        "REACT_FROM_TS": previous.get("REACT_FROM_TS") or f"{time.time():.6f}",
        "SEND_REMINDERS": "true" if body.get("send_reminders") else "false",
        "REMINDER_TEXT": reminder_text(body.get("reminder_text")),
        "DEADLINE_TZ": DEADLINE_TZ,
        "DEADLINE_TIME": deadline_time,
        "WEEK_ONE_MONDAY": body["week_one_monday"],
        "CLASSIFIER": body["classifier"],
        "SUMMARIZER": body["summarizer"],
        "LINKER": linker_name,
        **provider,
    }
    if local():
        # the server checks the same things again before it lets this computer use the bot
        gateway("register", {"channel": channel, "list": found["list"], "researchers": researchers,
                             "week_one_monday": body["week_one_monday"], "settings": shared(values)})
    teams.write_env(path, values)
    if moving_here:
        teams.profile_path(team).unlink()
    if not local():
        (teams.folder(team) / "notes.json").unlink(missing_ok=True)
    # save and start, so a stopped bot starts again
    if not local():
        teams.set_paused(team, False)
    if not local():
        announce(f"*team {team} {'moved here from its computer' if moving_here else 'updated' if previous else 'set up'}* by <@{session['user']}>,"
                 f" channel <#{channel}>, the supervisor starts it within seconds")


def announce(text):
    slack_http.post_json(deployment["SLACK_BOT_TOKEN"], "chat.postMessage", {"channel": deployment["OPERATOR_USER"], "text": text})


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


def rules_path(session):
    return teams.folder(session["team"]) / "regex-rules.txt"


def rules(session):
    path = rules_path(session)
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.is_file() else []


def llm_settings(module, prefix, provider, model):
    if not module["providers"]:
        return {}
    if provider not in module["providers"]:
        raise Problem("pick which LLM to use")
    model = (model or "").strip()
    if model and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.:_/\[\]-]{0,99}", model):
        raise Problem("that model name does not look right")
    # with limits the account's default could be a blocked model, so a model has to be named
    if provider != "local" and provider_limits() is not None and not model:
        raise Problem("pick a model")
    if provider != "local" and model and blocked(provider, model):
        raise Problem(f"{model} isn't available here, pick another model")
    # on a computer the cli has to be there to run the team
    if local() and provider in runners.COMMANDS and not runners.installed()[provider]:
        raise Problem(f"{module['providers'][provider]} isn't installed on this computer")
    return {f"{prefix}_PROVIDER": provider, f"{prefix}_MODEL": model}


def github_repo(link):
    link = (link or "").strip()
    if not link:
        return ""
    # a link to anything inside the repository names it first
    match = re.fullmatch(rf"(?:https?://)?(?:www\.)?(?:github\.com/)?({GITHUB_NAME})/([A-Za-z0-9._-]{{1,100}}?)(?:\.git)?(?:[/?#].*)?", link)
    if not match:
        raise Problem("that does not look like a GitHub repository link, like https://github.com/owner/repository")
    return f"{match.group(1)}/{match.group(2)}"


def github_people(repo):
    # github's contributor list only counts commits on the default branch, so pull request authors are added
    cached = github_members_cache.get(repo.lower())
    if cached and time.time() - cached[0] < GITHUB_MEMBERS_SECONDS:
        return cached[1]
    people = {}
    for kind in ("pulls?state=all&", "contributors?"):
        for page in range(1, GITHUB_MEMBER_PAGES + 1):
            request = urllib.request.Request(
                f"https://api.github.com/repos/{repo}/{kind}per_page=100&page={page}",
                headers={"Accept": "application/vnd.github+json", "User-Agent": "report-bot"},
            )
            try:
                with urllib.request.urlopen(request, timeout=15) as resp:
                    # an empty repository answers with no body
                    batch = json.loads(resp.read() or b"[]")
            except urllib.error.HTTPError as error:
                raise Problem(f"there is no public GitHub repository {repo}" if error.code == 404
                              else f"GitHub answered {error.code}, try again in a few minutes")
            except OSError:
                raise Problem("could not reach GitHub")
            for entry in batch:
                user = entry.get("user") or entry
                if user.get("login") and user.get("type") != "Bot":
                    people.setdefault(user["login"].lower(), {"login": user["login"], "avatar": user.get("avatar_url")})
            if len(batch) < 100:
                break
    found = sorted(people.values(), key=lambda m: m["login"].lower())
    github_members_cache[repo.lower()] = (time.time(), found)
    return found


def github_settings(link, handles, researchers):
    repo = github_repo(link)
    handles = {uid: (handle or "").strip() for uid, handle in (handles or {}).items() if uid in researchers and (handle or "").strip()}
    for handle in handles.values():
        if not re.fullmatch(GITHUB_NAME, handle):
            raise Problem(f"{handle} is not a GitHub username")
    if not repo:
        raise Problem("enter your GitHub repository, or set Work links to None")
    return {"GITHUB_REPO": repo, **({"GITHUB_HANDLES": ",".join(f"{uid}={h}" for uid, h in handles.items())} if handles else {})}


def reminder_text(text):
    text = " ".join((text or "").split())
    if len(text) > 1000:
        raise Problem("the reminder is too long, keep it under 1000 characters")
    return text or teams.DEFAULT_REMINDER


def week_pattern(pattern):
    pattern = (pattern or "").strip() or DEFAULT_WEEK_PATTERN
    try:
        compiled = re.compile(pattern)
    except re.error as error:
        raise Problem(f"the week pattern is not valid: {error}")
    if compiled.groups < 1:
        raise Problem("the week pattern needs a group in brackets around the number, like (\\d+)")
    return pattern


def local_model_settings(body):
    found = {key: str(body.get(key.lower()) or "").strip() for key in
             ("LOCAL_LLM_URL", "LOCAL_LLM_MODEL", "LOCAL_LLM_KEY", "LOCAL_LLM_TEMPERATURE", "LOCAL_LLM_MAX_TOKENS", "LOCAL_LLM_TIMEOUT")}
    if not found["LOCAL_LLM_URL"] or not found["LOCAL_LLM_MODEL"]:
        raise Problem("fill in your local model's server address and name")
    if urllib.parse.urlsplit(found["LOCAL_LLM_URL"]).scheme not in ("http", "https"):
        raise Problem("the local model's server address should start with http:// or https://")
    for key, kind, low, high, label in (("LOCAL_LLM_TEMPERATURE", float, 0, 2, "temperature"), ("LOCAL_LLM_MAX_TOKENS", int, 1, 1_000_000, "max output tokens"),
                                        ("LOCAL_LLM_TIMEOUT", int, 10, 3600, "timeout")):
        if found[key]:
            try:
                value = kind(found[key])
            except ValueError:
                raise Problem(f"the local model's {label} should be a number")
            if not low <= value <= high:
                raise Problem(f"the local model's {label} should be between {low} and {high}")
    return found


DESCRIPTIONS = {"report": "report-definition.md", "summary": "summary-style.md", "links": "links-rules.md"}


def definition_path(session, which):
    if which not in DESCRIPTIONS:
        raise Problem("unknown description")
    return teams.folder(session["team"]) / DESCRIPTIONS[which]


def definition(session, which):
    path = definition_path(session, which)
    default = (teams.ROOT / "processors" / DESCRIPTIONS[which]).read_text(encoding="utf-8")
    return {"text": path.read_text(encoding="utf-8") if path.is_file() else default, "default": default, "custom": path.is_file()}


def save_definition(session, which, text):
    text = (text or "").strip()
    default = definition(session, which)["default"]
    if len(text) > 20000:
        raise Problem("keep the description under 20,000 characters")
    path = definition_path(session, which)
    if not text or text == default.strip():
        # back to the default, which then follows any improvement to it
        path.unlink(missing_ok=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8", newline="\n")
    return definition(session, which)


model_lists = {}
MODEL_LIST_SECONDS = 300


def provider_limits():
    # read each time so an edit to it applies without a restart
    return json.loads(teams.PROVIDER_LIMITS.read_text(encoding="utf-8")) if teams.PROVIDER_LIMITS.is_file() else None


def blocked(provider, model):
    # a blocked name blocks every model whose name contains it
    names = ((provider_limits() or {}).get(provider) or {}).get("blocked") or []
    return any(name.lower() in model.lower() for name in names)


def model_list(provider, url=None, key=None):
    if provider == "local":
        return runners.local_models(url, key) if url else []
    cached = model_lists.get(provider)
    if not cached or time.time() - cached[0] > MODEL_LIST_SECONDS:
        found = runners.models(provider)
        known = {i for i, _ in found}
        found += [(name, name.title()) for name in offered_models().get(provider, []) if name not in known]
        model_lists[provider] = cached = (time.time(), found)
    return [(i, n) for i, n in cached[1] if not blocked(provider, i)]


def offered_models():
    # some CLIs cannot list their own models
    if local():
        try:
            return gateway("offered")["models"]
        except Problem:
            return {}
    return {provider: limits.get("models") or [] for provider, limits in (provider_limits() or {}).items()}


def save_rules(session, data):
    try:
        lines = [line.strip() for line in data.decode("utf-8").splitlines() if line.strip()]
    except UnicodeDecodeError:
        raise Problem("the rules file must be plain text")
    if not lines:
        raise Problem("the rules file has no patterns in it")
    for number, line in enumerate(lines, 1):
        try:
            re.compile(line)
        except re.error as error:
            raise Problem(f"pattern {number} `{line}` is not valid: {error}")
    rules_path(session).parent.mkdir(parents=True, exist_ok=True)
    rules_path(session).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return lines


def state(session):
    team = session["team"]
    path = teams.env_path(team)
    found = {
        "team": team,
        "values": teams.read_env(path) if path.is_file() else {},
        "classifiers": modules("classify"),
        "summarizers": modules("summarize"),
        "linkers": modules("find_links"),
        "week_pattern": DEFAULT_WEEK_PATTERN,
        "reminder_text": teams.DEFAULT_REMINDER,
        "provider_notes": {k: v["note"] for k, v in (provider_limits() or {}).items() if v.get("note")},
        "rules": rules(session),
    }
    if local():
        greeting = hello()
        return {**found, "mode": "local", "user": greeting["user"], "workspace": greeting["workspace"],
                "status": bot_status(None),
                "installed": runners.installed()}
    running = "local" if teams.profile_path(team).is_file() else "server" if path.is_file() else None
    return {**found, "mode": "server", "running": running, "app": teams.app_status(team), "status": bot_status(team),
            "user": lookup(session["user"]), "workspace": workspace_url()}


def signed_by_slack(headers, body):
    secret = deployment.get("SLACK_SIGNING_SECRET", "")
    stamp = headers.get("X-Slack-Request-Timestamp", "")
    if not secret or not stamp.isdigit() or abs(time.time() - int(stamp)) > 300:
        return False
    expected = "v0=" + hmac.new(secret.encode(), f"v0:{stamp}:".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, headers.get("X-Slack-Signature", ""))


def remember_join(channel, inviter, ts, replace=True):
    with joins_lock:
        joins = json.loads(teams.JOINS.read_text(encoding="utf-8")) if teams.JOINS.is_file() else {}
        if replace or channel not in joins:
            joins[channel] = {"inviter": inviter, "ts": ts}
        teams.JOINS.parent.mkdir(parents=True, exist_ok=True)
        teams.write_json(teams.JOINS, joins)


def record_join(event):
    # only the bot's own arrival in a channel matters, it names the person who invited it
    if event.get("type") != "message" or event.get("subtype") not in ("channel_join", "group_join"):
        return
    if event.get("user") != deployment["BOT_USER_ID"] or not event.get("inviter"):
        return
    remember_join(event["channel"], event["inviter"], event.get("ts"))


def find_join(channel):
    # reads back through a channel until the bot's invitation, which names who invited it
    cursor = None
    while True:
        params = {"channel": channel, "limit": 200}
        if cursor:
            params["cursor"] = cursor
        try:
            result = slack("conversations.history", params)
        except slack_http.SlackError:
            return None
        for m in result["messages"]:
            if m.get("subtype") in ("channel_join", "group_join") and m.get("user") == deployment["BOT_USER_ID"]:
                return m
        cursor = result.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            return None
        time.sleep(BACKFILL_PAUSE)


def backfill():
    # joins from before events were switched on, found through the files the bot can see, one page at a time
    state = json.loads(teams.BACKFILL.read_text(encoding="utf-8")) if teams.BACKFILL.is_file() else {"page": 1, "checked": []}
    checked = set(state["checked"])
    while state["page"]:
        found = slack("files.list", {"count": 200, "page": state["page"]})
        channels = set()
        for f in found.get("files", []):
            channels.update(f.get("groups", []), f.get("channels", []))
            for place in ("private", "public"):
                for channel, shares in f.get("shares", {}).get(place, {}).items():
                    channels.add(channel)
                    if shares and shares[0].get("channel_name"):
                        channel_names.setdefault(channel, shares[0]["channel_name"])
        for channel in sorted(channels - checked):
            join = find_join(channel)
            if join and join.get("inviter"):
                remember_join(channel, join["inviter"], join.get("ts"), replace=False)
            checked.add(channel)
            time.sleep(BACKFILL_PAUSE)
        pages = found.get("paging", {}).get("pages", 0)
        state = {"page": state["page"] + 1 if state["page"] < pages else 0, "checked": sorted(checked)}
        teams.write_json(teams.BACKFILL, state)


def claimed_channels(team):
    # channels other teams use, a team's own channel stays available to it
    claimed = set()
    for name in teams.names():
        if name != team:
            claimed.add(teams.read_env(teams.env_path(name)).get("SLACK_CHANNEL_ID"))
    for profile in teams.TEAMS_DIR.glob("*/profile.json") if teams.TEAMS_DIR.is_dir() else []:
        if profile.parent.name != team:
            claimed.add(json.loads(profile.read_text(encoding="utf-8")).get("channel"))
    return claimed


def look_at(channel):
    # whether the bot can still read the channel, and its name while there
    if time.time() - name_checked.get(channel, 0) > NAME_RECHECK_SECONDS:
        name_checked[channel] = time.time()
        try:
            messages = slack("conversations.history", {"channel": channel, "limit": 200})["messages"]
        except slack_http.SlackError as error:
            if error.result.get("error") in ("channel_not_found", "not_in_channel", "is_archived"):
                gone.add(channel)
            return
        gone.discard(channel)
        shared = next((f["id"] for m in messages for f in m.get("files", []) if f.get("id")), None)
        if shared and channel not in channel_names:
            learn_channel_name(channel, shared)


def forget_joins(channels):
    with joins_lock:
        joins = json.loads(teams.JOINS.read_text(encoding="utf-8")) if teams.JOINS.is_file() else {}
        for channel in channels:
            joins.pop(channel, None)
        teams.write_json(teams.JOINS, joins)


def joined(user, team):
    # every channel this person added the bot to that no other team uses, newest first
    if local():
        return gateway("joined")["channels"]
    joins = json.loads(teams.JOINS.read_text(encoding="utf-8")) if teams.JOINS.is_file() else {}
    mine = sorted(((float(j.get("ts") or 0), channel) for channel, j in joins.items() if j["inviter"] == user), reverse=True)
    claimed = claimed_channels(team)
    offered = [(ts, channel) for ts, channel in mine if channel not in claimed]
    for _, channel in offered:
        look_at(channel)
    lost = [channel for _, channel in offered if channel in gone]
    if lost:
        forget_joins(lost)
    return [{"id": channel, "name": channel_names.get(channel), "invited": ts} for ts, channel in offered if channel not in gone]


def channel_lists(channel, user, team):
    # files.list takes a lists type that slack's docs leave out, it returns the lists shared in the channel
    if local():
        return gateway(f"lists?channel={urllib.parse.quote(channel or '')}")["lists"]
    allowed = {c["id"] for c in joined(user, team)}
    path = teams.env_path(team)
    if path.is_file():
        allowed.add(teams.read_env(path).get("SLACK_CHANNEL_ID"))
    if channel not in allowed:
        raise Problem("invite the bot to your channel first")
    found = []
    for page in range(1, LIST_PAGES + 1):
        result = slack("files.list", {"channel": channel, "types": "lists", "count": 100, "page": page})
        found += [{"id": f["id"], "title": f.get("title") or f.get("name") or f["id"], "updated": f.get("updated") or f.get("created") or 0}
                  for f in result.get("files", [])]
        if page >= (result.get("paging") or {}).get("pages", 1):
            break
    return sorted(found, key=lambda f: -f["updated"])


def location(team):
    # a team is set to run in one place at a time, the app on a computer or this server
    if teams.profile_path(team).is_file():
        return "computer"
    return "server" if teams.env_path(team).is_file() else None


def bot_status(team):
    if local():
        return gateway("status")
    notes = teams.folder(team) / "notes.json"
    return {"location": location(team), "paused": teams.paused(team), "app": teams.app_status(team),
            "notes": json.loads(notes.read_text(encoding="utf-8")) if notes.is_file() else []}


def control(team, user, action, where):
    if action == "stop":
        teams.set_paused(team, True)
        announce(f"*team {team} stopped* by <@{user}>")
    elif action == "start":
        if where == "server" and location(team) == "computer":
            take_over(team)
        elif where != location(team):
            raise Problem("start it from the app on that computer")
        teams.set_paused(team, False)
        announce(f"*team {team} started* on {'its own computer' if where == 'computer' else 'this server'} by <@{user}>")
    elif action == "dismiss":
        (teams.folder(team) / "notes.json").unlink(missing_ok=True)
    else:
        raise Problem("unknown action")
    return bot_status(team)


def shared(values):
    # what the server needs to take the team over, the local model's address and key stay on the computer
    return {k: v for k, v in values.items() if not k.startswith("LOCAL_LLM_")}


STEPS = {"LLM": "Classifier", "SUMMARY": "Summarizer", "LINKS": "Link to Work"}


def take_over(team):
    # the server runs a team the app was running with the settings the app last shared
    path = teams.folder(team) / "app-settings.env"
    if not path.is_file():
        raise Problem("the app hasn't shared its settings yet, open Settings and save them here")
    settings = teams.read_env(path)
    limits = provider_limits()
    names = constant(ast.parse((teams.ROOT / "processors" / "llm.py").read_text(encoding="utf-8")), "PROVIDERS")
    notes = []
    for prefix, step in STEPS.items():
        provider, model = settings.get(f"{prefix}_PROVIDER"), settings.get(f"{prefix}_MODEL") or ""
        if not provider or limits is None or (provider in limits and model and not blocked(provider, model)):
            continue
        # the nearest thing the server offers, its own first model for that cli, or claude's for one it doesn't run
        offered = [i for i, _ in model_list(provider)] if provider in limits else []
        new_provider, new_model = (provider, offered[0]) if offered else ("claude", ([i for i, _ in model_list("claude")] or ["sonnet"])[0])
        settings[f"{prefix}_PROVIDER"], settings[f"{prefix}_MODEL"] = new_provider, new_model
        was = f"{names.get(provider, provider)} {model}".strip()
        notes.append(f"{step} now uses {names.get(new_provider, new_provider)} {new_model} instead of {was}, which www.haagbot.lol doesn't offer.")
    teams.write_env(teams.env_path(team), settings)
    teams.profile_path(team).unlink()
    teams.write_json(teams.folder(team) / "notes.json", notes)


DATA = {"summary": stats.summary, "log": stats.log, "db": lambda team, names: stats.tables(team)}


def team_data(team, kind):
    # stats are read from the bot's own database, which lives wherever the bot runs
    if kind not in DATA:
        raise Problem("unknown data")
    names = lambda uid: lookup(uid)["name"] or uid
    if local():
        if gateway("status")["location"] != "computer":
            return gateway(f"data?kind={kind}")["data"]
        team = deployment.get("LOCAL_TEAM")
    elif location(team) == "computer":
        raise Problem("No stats available. Last run was local.")
    if not team or not teams.env_path(team).is_file():
        raise Problem("save the settings first")
    return DATA[kind](team, names)


def local_control(action, where):
    # the app moves a team the server runs to this computer with the settings saved here, then starts it
    if action == "start" and where == "computer" and gateway("status")["location"] != "computer":
        team = deployment.get("LOCAL_TEAM")
        path = teams.env_path(team) if team else None
        if not path or not path.is_file():
            raise Problem("save the settings in this app first")
        saved = teams.read_env(path)
        gateway("register", {"channel": saved["SLACK_CHANNEL_ID"], "list": saved["SLACK_LIST_ID"],
                             "researchers": saved["RESEARCHERS"].split(), "week_one_monday": saved["WEEK_ONE_MONDAY"],
                             "settings": shared(saved)})
    return gateway("bot", {"action": action, "where": where})


def profile_guard(team):
    profile = json.loads(teams.profile_path(team).read_text(encoding="utf-8"))
    return Guard(profile["channel"], profile["list"], profile["people"])


def register(found, body):
    # a team on its own computer gets an allow-list here, after the same checks the setup page runs
    team = found["team"]
    if teams.env_path(team).is_file() and not teams.paused(team):
        raise Problem("the bot is running on www.haagbot.lol, stop it there first")
    channel, people = channel_check(body.get("channel"), body.get("week_one_monday"))
    list_id = list_check(body.get("list"))["list"]
    researchers = body.get("researchers") or []
    researchers_check(people, researchers)
    settings = body.get("settings")
    if not isinstance(settings, dict) or not all(isinstance(k, str) and isinstance(v, str) and "\n" not in k + v
                                                  for k, v in settings.items()):
        raise Problem("the app sent its settings in a form the server can't read, update the app")
    teams.write_env(teams.folder(team) / "app-settings.env", settings)
    path = teams.profile_path(team)
    first = not path.is_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"channel": channel, "list": list_id, "people": sorted(set(researchers) | {found["user"]})}),
                    encoding="utf-8")
    # saving in the app moves a team this server was running to the app, the supervisor stops it here
    moved = teams.env_path(team).is_file()
    if moved:
        teams.env_path(team).unlink()
    teams.set_paused(team, False)
    announce(f"*team {team} {'moved to its own computer' if moved else 'set up to run on its own computer' if first else 'updated on its own computer'}* by <@{found['user']}>,"
             f" channel <#{channel}>")


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
        try:
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            # the page or app went away before the answer, a reload or a closed window, nothing to do
            pass

    def route(self):
        parts = self.path.split("?")[0].strip("/").split("/")
        return "/".join(parts[1:]) if parts[0] == "setup" else None

    def trusted(self):
        # on a researcher's computer only pages this app served may call it, other sites and rebound names may not
        if not local():
            return True
        port = self.server.server_address[1]
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        origin = self.headers.get("Origin")
        return self.headers.get("Host") in hosts and (origin is None or origin.split("//", 1)[-1] in hosts)

    def session(self):
        cookie = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
        key = cookie[COOKIE].value if COOKIE in cookie else ""
        return key, teams.find_session(key)

    def cookie(self, key):
        # lax rather than strict so the page still opens from a link clicked in slack
        return ("Set-Cookie", f"{COOKIE}={key}; Path=/setup; Max-Age={teams.SESSION_DAYS * 86400};"
                              " HttpOnly; Secure; SameSite=Lax")

    def gone(self, token=None):
        # a link the server issued can ask for a new one, an unknown one cannot say who is asking
        invite = None if local() else teams.invite_record(token)
        if not invite:
            ask = '<p class="muted">Ask the person who invited you for a new link.</p>'
        elif invite.get("requested"):
            ask = '<p class="status ok">You already asked. A new link will arrive as a Slack DM.</p>'
        else:
            ask = ('<button type="button" id="request">Request a new invite</button>'
                   '<p class="status" id="request-status" role="status"></p>')
        page = (PAGE / "gone.html").read_text(encoding="utf-8").replace("<!--request-->", ask)
        self.send(404, page.encode(), "text/html; charset=utf-8")

    def request_invite(self, token):
        invite = teams.invite_record(token)
        if not invite:
            return self.send(404, {"error": "not found"})
        if not invite.get("requested"):
            teams.mark_requested(token)
            how = "was already used" if invite.get("used") else "expired"
            announce(f"*<@{invite['user']}> asked for a new setup link* for team `{invite['team']}`, their last one {how}.\n"
                     f"Send one with `report-bot-app invite {invite['user']} --team {invite['team']} --data {teams.DATA_DIR}`")
        self.send(200, {"ok": True})

    def app_key(self):
        auth = self.headers.get("Authorization", "")
        return teams.find_app_key(auth[7:]) if auth.startswith("Bearer ") else None

    def gateway_get(self, kind):
        found = self.app_key()
        if not found:
            return self.send(401, {"error": "this app key is not valid, connect the app again from the setup page"})
        if kind == "gateway/offered":
            return self.send(200, {"models": offered_models()})
        if kind == "gateway/joined":
            return self.send(200, {"channels": joined(found["user"], found["team"])})
        if kind == "gateway/lists":
            channel = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query)).get("channel")
            try:
                return self.send(200, {"lists": channel_lists(channel, found["user"], found["team"])})
            except Problem as problem:
                return self.send(400, {"error": str(problem)})
        if kind == "gateway/status":
            return self.send(200, bot_status(found["team"]))
        if kind == "gateway/data":
            query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
            try:
                return self.send(200, {"data": team_data(found["team"], query.get("kind"))})
            except Problem as problem:
                return self.send(400, {"error": str(problem)})
        if kind == "gateway/hello":
            return self.send(200, {"team": found["team"], "user": lookup(found["user"]), "workspace": workspace_url(),
                                   "bot_user_id": deployment["BOT_USER_ID"]})
        if kind.startswith("gateway/files/") and teams.profile_path(found["team"]).is_file():
            try:
                info = slack("files.info", {"file": kind.split("/")[2]})["file"]
                profile_guard(found["team"]).check_file(info)
            except (slack_http.SlackError, Refused) as error:
                return self.send(403, {"error": str(error)})
            return self.send(200, slack_http.download(deployment["SLACK_BOT_TOKEN"], info["url_private"]),
                             "application/octet-stream")
        self.send(404, {"error": "not found"})

    def gateway_post(self, kind):
        found = self.app_key()
        if not found:
            return self.send(401, {"error": "this app key is not valid, connect the app again from the setup page"})
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1024 * 1024:
            return self.send(413, {"error": "too large"})
        data = self.rfile.read(length)
        if kind.startswith("gateway/slack/"):
            return self.forward(found["team"], kind[len("gateway/slack/"):], data)
        body = json.loads(data or b"{}")
        try:
            if kind == "gateway/check/channel":
                channel, people = channel_check(body.get("link"), body.get("week_one_monday"))
                return self.send(200, {"channel": channel, "people": people, "name": channel_names.get(channel)})
            if kind == "gateway/check/list":
                return self.send(200, list_check(body.get("link")))
            if kind == "gateway/bot":
                return self.send(200, control(found["team"], found["user"], body.get("action"), body.get("where")))
            if kind == "gateway/register":
                register(found, body)
                return self.send(200, {"team": found["team"]})
            if kind == "gateway/presence":
                teams.app_window(self.headers["Authorization"][7:], body.get("open") is True)
                return self.send(200, {"ok": True})
        except Problem as problem:
            return self.send(400, {"error": str(problem)})
        self.send(404, {"error": "not found"})

    def forward(self, team, method, data):
        # the bot token never leaves this server, every call is checked against the team's allow-list first
        content_type = self.headers.get("Content-Type", "")
        if content_type.startswith("application/json"):
            params = json.loads(data or b"{}")
        else:
            params = {k: v[0] for k, v in urllib.parse.parse_qs(data.decode()).items()}
        if not teams.profile_path(team).is_file():
            return self.send(200, {"ok": False, "error": "not_set_up", "detail": "save the setup page in the app first"})
        if teams.paused(team):
            return self.send(200, {"ok": False, "error": "paused", "detail": "the bot was stopped from its dashboard"})
        try:
            profile_guard(team).check(method, params)
        except Refused as refused:
            return self.send(200, {"ok": False, "error": "guard_refused", "detail": str(refused)})
        request = urllib.request.Request(f"{slack_http.SLACK}/{method}", data, {
            "Authorization": f"Bearer {deployment['SLACK_BOT_TOKEN']}", "Content-Type": content_type,
        })
        try:
            with urllib.request.urlopen(request, timeout=30) as resp:
                return self.send(resp.status, resp.read())
        except urllib.error.HTTPError as error:
            return self.send(error.code, error.read(), headers=[("Retry-After", error.headers.get("Retry-After", "30"))])

    def do_GET(self):
        kind = self.route()
        if kind is None or not self.trusted():
            return self.send(404, {"error": "not found"})
        if kind.startswith("gateway/") and not local():
            return self.gateway_get(kind)
        if kind.startswith("static/") and kind[7:] in STATIC:
            return self.send(200, (PAGE / kind[7:]).read_bytes(), STATIC[kind[7:]])
        if kind == "connect" and local():
            return self.send(200, (PAGE / "connect.html").read_bytes(), "text/html; charset=utf-8")
        key, session = self.session()
        if kind and not kind.startswith("api/"):
            # an invite works once and belongs to the first browser that opens it
            invite = teams.claim_invite(kind)
            if invite:
                key, session = teams.create_session(invite["team"], invite["user"]), invite
            if not session:
                return self.gone(kind)
            return self.send(303, b"", "text/plain", [("Location", "/setup/"), self.cookie(key)])
        if not session:
            if not kind and local():
                return self.send(303, b"", "text/plain", [("Location", "/setup/connect")])
            return self.gone() if not kind else self.send(401, {"error": "this setup session has ended, ask for a new invite"})
        if not kind:
            return self.send(200, (PAGE / "index.html").read_bytes(), "text/html; charset=utf-8", [self.cookie(key)])
        try:
            if kind == "api/state":
                return self.send(200, state(session))
            if kind == "api/examples":
                return self.send(200, examples(session))
            if kind == "api/models":
                query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
                found = model_list(query.get("provider"), query.get("url"), query.get("key"))
                return self.send(200, {"models": [{"id": i, "name": n} for i, n in found],
                                       "account_default": provider_limits() is None})
            if kind == "api/installed" and local():
                # whatever was just installed should list its models straight away
                model_lists.clear()
                return self.send(200, {"installed": runners.installed()})
            if kind == "api/github":
                repo = github_repo(dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query)).get("repo"))
                return self.send(200, {"repo": repo, "members": github_people(repo) if repo else []})
            if kind == "api/definition":
                which = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query)).get("which", "report")
                return self.send(200, definition(session, which))
            if kind == "api/joined":
                return self.send(200, {"channels": joined(session["user"], session["team"])})
            if kind == "api/lists":
                channel = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query)).get("channel")
                return self.send(200, {"lists": channel_lists(channel, session["user"], session["team"])})
            if kind == "api/status":
                return self.send(200, bot_status(session["team"]))
            if kind == "api/data":
                query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
                return self.send(200, {"data": team_data(session["team"], query.get("kind"))})
            if kind == "api/app-status" and not local():
                return self.send(200, {"app": teams.app_status(session["team"])})
        except Problem as problem:
            return self.send(400, {"error": str(problem)})
        self.send(404, {"error": "not found"})

    def do_POST(self):
        kind = self.route() or ""
        if not self.trusted():
            return self.send(404, {"error": "not found"})
        if kind.startswith("gateway/") and not local():
            return self.gateway_post(kind)
        length = int(self.headers.get("Content-Length") or 0)
        asked = re.fullmatch(r"([A-Za-z0-9_-]{20,})/request", kind)
        if asked and not local():
            return self.request_invite(asked.group(1))
        if kind == "events" and not local():
            body = self.rfile.read(length) if length <= 1024 * 1024 else b""
            if not signed_by_slack(self.headers, body):
                return self.send(401, {"error": "not signed by slack"})
            event = json.loads(body or b"{}")
            if event.get("type") == "url_verification":
                return self.send(200, {"challenge": event.get("challenge")})
            record_join(event.get("event") or {})
            return self.send(200, {"ok": True})
        if kind == "api/window" and on_window:
            on_window(json.loads(self.rfile.read(length) or b"{}").get("open") is True if length <= 1024 else False)
            return self.send(200, {"ok": True})
        if kind == "api/connect" and on_connect:
            body = json.loads(self.rfile.read(length) or b"{}") if length <= 65536 else {}
            try:
                return self.send(200, {"open": on_connect(body.get("key"), body.get("server"))})
            except Problem as problem:
                return self.send(400, {"error": str(problem)})
        key, session = self.session()
        if not session:
            return self.send(401, {"error": "this setup session has ended, ask for a new invite"})
        if kind.startswith("api/examples/"):
            if length > EXAMPLE_BYTES:
                return self.send(413, {"error": "examples can be at most 100 MB"})
            name = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("name", [""])[0]
            try:
                add_example(session, key, kind.split("/")[2], name, self.rfile.read(length))
            except Problem as problem:
                return self.send(400, {"error": str(problem)})
            return self.send(200, examples(session))
        if kind == "api/rules":
            if length > RULES_BYTES:
                return self.send(413, {"error": "the rules file can be at most 1 MB"})
            try:
                return self.send(200, {"rules": save_rules(session, self.rfile.read(length))})
            except Problem as problem:
                return self.send(400, {"error": str(problem)})
        if length > 65536:
            return self.send(413, {"error": "too large"})
        body = json.loads(self.rfile.read(length) or b"{}")
        try:
            if kind == "api/channel":
                channel, people = channel_check(body.get("link"), body.get("week_one_monday"))
                active[key] = {p["id"] for p in people}
                return self.send(200, {"channel": channel, "people": people, "name": channel_names.get(channel)})
            if kind == "api/list":
                return self.send(200, list_check(body.get("link")))
            if kind == "api/definition":
                return self.send(200, save_definition(session, body.get("which", "report"), body.get("text")))
            if kind == "api/bot":
                if local():
                    return self.send(200, local_control(body.get("action"), body.get("where")))
                return self.send(200, control(session["team"], session["user"], body.get("action"), body.get("where")))
            if kind == "api/app-key" and not local():
                return self.send(200, {"key": teams.create_app_key(session["team"], session["user"])})
            if kind == "api/save":
                save(session, body)
                return self.send(200, {"team": session["team"]})
        except Problem as problem:
            return self.send(400, {"error": str(problem)})
        self.send(404, {"error": "not found"})

    def do_DELETE(self):
        parts = (self.route() or "").split("/")
        if not self.trusted():
            return self.send(404, {"error": "not found"})
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


reload()
