import difflib
import re

# what the bot needs from a list, which column types it can write each role into, and how column names tend to read
ROLES = {
    "COL_WEEK": {
        "label": "Week", "optional": False, "types": ("text", "date"),
        "names": [(r"\bweek", 5), (r"\bwk\b", 4), (r"\b(period|reporting|sprint)\b", 3), (r"\b(date|due|deadline|submitted|when)\b", 2)],
    },
    "COL_RESEARCHER": {
        "label": "Researcher", "optional": False, "types": ("user", "text"),
        "names": [(r"research", 5), (r"\b(student|member|person|people|author|owner|assignee|reporter|submitter|who)\b", 3), (r"\bname\b", 3)],
    },
    "COL_SUMMARY": {
        "label": "Summary", "optional": False, "types": ("text",),
        "names": [(r"summar", 5), (r"\b(progress|accomplish\w*|done|did|highlights?|updates?|notes|report)\b", 3), (r"\bwork\b", 2)],
    },
    "COL_BLOCKERS": {
        "label": "Blockers", "optional": False, "types": ("text",),
        "names": [(r"block", 5), (r"\b(needs?|issues?|imped\w*|risks?|problems?|help|challenges?|obstacles?|stuck)\b", 3)],
    },
    "COL_STATUS": {
        "label": "Status", "optional": False, "types": ("select", "text"),
        "names": [(r"status", 5), (r"\b(state|health|track|rag)\b", 3)],
    },
    "COL_CONFIRMED": {
        "label": "Confirmed", "optional": True, "types": ("checkbox",),
        "names": [(r"confirm", 5), (r"\b(review\w*|approv\w*|verif\w*|checked|graded|acknowledg\w*|seen|ok)\b", 3)],
    },
    "COL_LINK": {
        "label": "Link to Work", "optional": True, "types": ("text",),
        "names": [(r"\blinks?\b|\burls?\b", 5), (r"\b(pr|prs|pull|github|repo|commits?|evidence|code)\b", 3)],
    },
}
# a role takes a column whose name scores at least this, so an unrelated column is never assigned
THRESHOLD = 2
CLOSE_SPELLING = 0.8


def words(name):
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def score(key, column):
    role = ROLES[key]
    if column["type"] not in role["types"]:
        return 0
    name = words(column["name"])
    best = max((points for pattern, points in role["names"] if re.search(pattern, name)), default=0)
    # close spellings of the role's own name, such as Blocker or Sumary
    if difflib.SequenceMatcher(None, name, role["label"].lower()).ratio() >= CLOSE_SPELLING:
        best = max(best, 5)
    # a column of the role's first type fits slightly better than one the bot converts into
    return best + (1 if best and column["type"] == role["types"][0] else 0)


def match(schema):
    # strongest pairs first and each column used once
    pairs = sorted(((score(key, column), index, key, column["id"]) for key in ROLES for index, column in enumerate(schema)),
                   key=lambda pair: (-pair[0], pair[1]))
    chosen, used = {}, set()
    for points, _, key, column_id in pairs:
        if points >= THRESHOLD and key not in chosen and column_id not in used:
            chosen[key] = column_id
            used.add(column_id)
    return chosen
