import logging
import sys
import time

import teams

teams.take_team_arg(sys.argv)

import store
from config import ALLOWED_POSTERS, CLASSIFIER, LINKER, LIST_ID, NOT_APPLICABLE, SUMMARIZER, TEAM
from deadline import check_deadline
from processors import check_classification, check_links, check_summary, document, load
from reactions import react
from reconcile import reconcile
from slack_api import fetch_since, notify
from weeks import resolve_week, week_label

POLL_SECONDS = 60

logging.basicConfig(level=logging.INFO, format=f"%(levelname)s [{TEAM}] %(message)s")
log = logging.getLogger("slack-list-bot")

classify = load(CLASSIFIER, "classify")
summarize = load(SUMMARIZER, "summarize")
find_links = load(LINKER, "find_links")


def bullet_lines(label, bullets):
    if bullets is None:
        return f"{label:<13}unset\n"
    if bullets == NOT_APPLICABLE:
        return f"{label:<13}{NOT_APPLICABLE}\n"
    return f"{label:<13}{len(bullets)} bullets\n" + "".join(f"             • {b}\n" for b in bullets)


def handle_file(msg, slack_file):
    started = time.monotonic()
    head = notify(
        f"<@{msg['user']}> posted *{slack_file['name']}*\n"
        f"`{slack_file.get('mimetype')}  {slack_file.get('size', 0):,} bytes  ts {msg['ts']}`\nprocessing..."
    )
    try:
        notify(f"classifying with `{CLASSIFIER}`", head)
        doc = document(msg, slack_file)
        classification = classify(doc)
        check_classification(classification)
        for note in classification.notes:
            notify(note, head)
        if not classification.is_report:
            notify(f"*not a report*, no row created  _{time.monotonic() - started:.0f}s_", head)
            return
        notify(f"*is a report*, summarizing with `{SUMMARIZER}`", head)
        summary = summarize(doc)
        check_summary(summary)
        for note in summary.week.notes + summary.notes:
            notify(note, head)
        week, source = resolve_week(summary.week, msg["ts"])
        notify(f"*week {week}*, {source}", head)
        links = find_links(doc, summary, week)
        check_links(links)
        for note in links.notes:
            notify(note, head)

        existing = store.get_report(msg["user"], week)
        action = "resubmitted" if existing else "report_filed"
        if existing:
            notify(
                f"*replacing* existing entry for <@{msg['user']}> week {week},"
                f" was {existing['status'] or 'unset'}, row `{existing['row_id']}`",
                head,
            )
        store.save_report(
            action, msg["user"], week,
            summary.status, summary.summary, summary.blockers, links.urls,
            source_file=slack_file["id"], source_ts=msg["ts"], cause=slack_file["name"],
        )
        notify(
            f"Week         {week_label(week)}\n"
            f"Researcher   <@{msg['user']}>\n"
            f"Status       {summary.status or 'unset'}\n"
            + bullet_lines("Summary", summary.summary)
            + bullet_lines("Blockers", summary.blockers)
            + bullet_lines("Link to Work", links.urls),
            head,
        )
        reconcile(head)
        row = store.get_report(msg["user"], week)
        notify(f"*list updated*, row `{row['row_id']}` in list {LIST_ID}  _{time.monotonic() - started:.0f}s_", head)
        notify(react(msg, week, slack_file), head)
        log.info("stored %s %s week %s", slack_file["name"], msg["user"], week)
    except Exception as error:
        notify(f"*failed*  `{type(error).__name__}: {error}`", head)
        log.exception("failed on %s", slack_file["name"])


def poll():
    for msg in fetch_since(store.get_cursor()):
        if msg.get("user") in ALLOWED_POSTERS:
            for slack_file in msg.get("files", []):
                handle_file(msg, slack_file)
        store.set_cursor(msg["ts"])
    # reconcile every poll since a person editing the list leaves nothing dirty locally
    for note in reconcile():
        log.info("reconcile: %s", note)
    check_deadline()


if __name__ == "__main__":
    if store.get_cursor() is None:
        store.set_cursor(f"{time.time():.6f}")
    while True:
        try:
            poll()
        except Exception:
            log.exception("poll failed")
        time.sleep(POLL_SECONDS)
