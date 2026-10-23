import re

from config import env
from processors import Week
from processors.text import posted_text

DEFAULT_PATTERN = r"(?i)\bweek\s*(\d+)"


def find_week(doc):
    # the first group of the first match is the week number
    pattern = env.get("WEEK_PATTERN") or DEFAULT_PATTERN
    found = re.search(pattern, posted_text(doc) or "")
    if not found or not found.group(1).isdigit() or int(found.group(1)) < 1:
        return Week(notes=(f"pattern `{pattern}` found no week",))
    return Week(int(found.group(1)), notes=(f"pattern `{pattern}` matched `{found.group(0)}`",))
