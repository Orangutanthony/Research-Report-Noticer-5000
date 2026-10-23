import slack_http
from config import ALLOWED_POSTERS, CHANNEL, DEBUG_DM_USER, GATEWAY_URL, LIST_ID, RESEARCHERS, TEAM, TOKEN
from guard import Guard

guard = Guard(CHANNEL, LIST_ID, set(RESEARCHERS) | ALLOWED_POSTERS | {DEBUG_DM_USER})
BASE = f"{GATEWAY_URL.rstrip('/')}/setup/gateway/slack" if GATEWAY_URL else slack_http.SLACK


def form(method, params):
    guard.check(method, params)
    return slack_http.form(TOKEN, method, params, BASE)


def post_json(method, body):
    guard.check(method, body)
    return slack_http.post_json(TOKEN, method, body, BASE)


def notify(text, thread_ts=None):
    body = {"channel": DEBUG_DM_USER, "text": text if thread_ts else f"`{TEAM}` {text}"}
    if thread_ts:
        body["thread_ts"] = thread_ts
    return post_json("chat.postMessage", body)["ts"]


def fetch_since(oldest):
    messages, cursor = [], None
    while True:
        params = {"channel": CHANNEL, "oldest": oldest, "limit": 200}
        if cursor:
            params["cursor"] = cursor
        result = form("conversations.history", params)
        messages += result["messages"]
        cursor = result.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            # ts compared as integer parts since float loses precision
            return sorted(messages, key=lambda m: tuple(map(int, m["ts"].split("."))))


def permalink(ts):
    return form("chat.getPermalink", {"channel": CHANNEL, "message_ts": ts})["permalink"]


def download(slack_file):
    if GATEWAY_URL:
        # the gateway checks the file was shared in this team's channel before handing it over
        return slack_http.download(TOKEN, f"{GATEWAY_URL.rstrip('/')}/setup/gateway/files/{slack_file['id']}")
    info = slack_http.form(TOKEN, "files.info", {"file": slack_file["id"]})["file"]
    guard.check_file(info)
    return slack_http.download(TOKEN, info["url_private"])
