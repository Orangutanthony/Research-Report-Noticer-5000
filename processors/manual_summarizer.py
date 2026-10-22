from processors import Summary
from slack_api import notify, permalink
from weeks import week_label


def summarize(doc, classification, week):
    notify(
        f"*summarize by hand*: <@{doc.poster}> {week_label(week)}, *{doc.filename}*\n"
        f"{permalink(doc.posted_ts)}\n"
        f"fill in Status, Summary and Blockers on its row in the List"
    )
    return Summary(notes=("asked for a summary by hand in a separate DM",))
