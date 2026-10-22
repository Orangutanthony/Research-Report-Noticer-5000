import json
import time
import urllib.error
import urllib.parse
import urllib.request


def request(token, method, data, content_type):
    req = urllib.request.Request(
        f"https://slack.com/api/{method}",
        data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": content_type},
    )
    while True:
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.load(resp)
        except urllib.error.HTTPError as error:
            if error.code != 429:
                raise
            time.sleep(int(error.headers.get("Retry-After", "30")) + 1)
            continue
        if result.get("error") == "ratelimited":
            time.sleep(30)
            continue
        if not result.get("ok"):
            raise RuntimeError(f"{method} {result}")
        return result


def form(token, method, params):
    return request(token, method, urllib.parse.urlencode(params).encode(), "application/x-www-form-urlencoded")


def post_json(token, method, body):
    return request(token, method, json.dumps(body).encode(), "application/json; charset=utf-8")


def download(token, url):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()
