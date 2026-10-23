from processors import Summary
from processors.regex_week import find_week
from slack_api import notify, permalink
from weeks import resolve_week, week_label

DESCRIPTION = "None (I will summarize it myself)"
ORDER = 2


def summarize(doc):
    found = find_week(doc)
    week, _ = resolve_week(found, doc.posted_ts)
    notify(
        f"*summarize by hand*: <@{doc.poster}> {week_label(week)}, *{doc.filename}*\n"
        f"{permalink(doc.posted_ts)}\n"
        f"fill in Status, Summary and Blockers on its row in the List"
    )
    return Summary(found, notes=("asked for a summary by hand in a separate DM",))
