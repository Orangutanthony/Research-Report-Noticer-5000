import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

import store
from config import GITHUB_HANDLES, GITHUB_REPO, WEEK_ONE_MONDAY

API = "https://api.github.com/search/issues"
PAGE_SIZE = 100
MAX_PAGES = 10
PAGE_PAUSE = 7
PR_WINDOW_BEFORE = 7
PR_WINDOW_AFTER = 2


def search(repo, author, since, page):
    query = f"is:pr repo:{repo} author:{author} updated:>={since}"
    params = {"q": query, "per_page": PAGE_SIZE, "page": page, "sort": "updated", "order": "asc"}
    req = urllib.request.Request(
        f"{API}?{urllib.parse.urlencode(params)}",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "report-bot"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def sync(repo, author, default_since):
    stored = 0
    since = store.pr_watermark(repo, author) or default_since
    highest = since
    for page in range(1, MAX_PAGES + 1):
        items = search(repo, author, since, page).get("items", [])
        for item in items:
            # merged_at lives only on the nested pull_request object in search results
            merged = (item.get("pull_request") or {}).get("merged_at")
            store.save_pr(
                repo=repo,
                number=item["number"],
                author=item["user"]["login"],
                title=item["title"],
                body=item.get("body"),
                state=item["state"],
                draft=bool(item.get("draft")),
                created_at=item["created_at"],
                updated_at=item["updated_at"],
                closed_at=item.get("closed_at"),
                merged_at=merged,
                url=item["html_url"],
            )
            stored += 1
            highest = max(highest, item["updated_at"])
        if len(items) < PAGE_SIZE:
            break
        time.sleep(PAGE_PAUSE)
    store.set_pr_watermark(repo, author, highest)
    return stored


def week_span(week):
    monday = WEEK_ONE_MONDAY + timedelta(weeks=week - 1)
    return monday - timedelta(days=PR_WINDOW_BEFORE), monday + timedelta(days=4 + PR_WINDOW_AFTER)


def candidates(repo, author, first, last):
    # updated_at churns on comments and labels so it does not place a pr in a week
    found = []
    for row in store.prs_by(repo, author):
        stamps = [row["created_at"], row["merged_at"]]
        dates = [datetime.strptime(s[:10], "%Y-%m-%d").date() for s in stamps if s]
        if any(first <= d <= last for d in dates):
            found.append(row)
    return found


def for_week(user, week, notes):
    if not GITHUB_REPO:
        notes.append("no github repository set, document links only")
        return []
    handle = GITHUB_HANDLES.get(user)
    if not handle:
        notes.append(f"no github username on file for <@{user}>, document links only")
        return []
    first, last = week_span(week)
    try:
        fetched = sync(GITHUB_REPO, handle, WEEK_ONE_MONDAY.isoformat())
        notes.append(f"github synced {fetched} prs by `{handle}` in `{GITHUB_REPO}`")
    except (OSError, ValueError, KeyError) as error:
        notes.append(f"*github sync failed* `{type(error).__name__}: {error}`, using the cache as it stands")
    found = candidates(GITHUB_REPO, handle, first, last)
    notes.append(f"{len(found)} pr candidates for `{handle}` between {first} and {last}")
    return found


def describe(rows, body_chars):
    if not rows:
        return "- (none found for this researcher around this week)"
    lines = []
    for row in rows:
        state = "merged" if row["merged_at"] else row["state"]
        lines.append(f"- {row['url']}")
        lines.append(f"    repo {row['repo']} pr #{row['number']}, {state}, created {row['created_at'][:10]}"
                     f", merged {(row['merged_at'] or 'not merged')[:10]}")
        lines.append(f"    title: {row['title']}")
        if row["body"]:
            lines.append(f"    description: {' '.join(row['body'].split())[:body_chars]}")
    return "\n".join(lines)
