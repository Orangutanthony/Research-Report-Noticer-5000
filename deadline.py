from datetime import datetime

import store
from config import DEADLINE_ENABLED, DEADLINE_TZ, DEBUG_DM_USER, NO_REPORT, NOT_APPLICABLE, RESEARCHERS
from reconcile import reconcile
from slack_api import notify, post_json
from weeks import closed_week, week_label


def remind(user, week):
    result = post_json("chat.postMessage", {
        "channel": user,
        "text": f"Don't forget to post your Week {week} report to the channel when you can :-)",
    })
    label = "weekly copy" if user == DEBUG_DM_USER else "did not file"
    notify(f"*reminder delivered* to <@{user}> for week {week} ({label}), ts `{result['ts']}`")


def check_deadline():
    if not DEADLINE_ENABLED:
        return
    week = closed_week(datetime.now(DEADLINE_TZ))
    if week < 1 or store.deadline_enforced(week):
        return
    missing = [r for r in RESEARCHERS if not store.get_report(r, week)]
    for researcher in missing:
        store.save_report(
            "marked_no_report", researcher, week, NO_REPORT, NOT_APPLICABLE, NOT_APPLICABLE, cause="deadline",
        )
        remind(researcher, week)
    # weekly copy so the wording stays visible even when everyone files
    remind(DEBUG_DM_USER, week)
    store.mark_deadline_enforced(week)
    filed = [r for r in RESEARCHERS if r not in missing]
    notify(
        f"*week {week} closed* ({week_label(week)})\n"
        f"filed: {', '.join(f'<@{r}>' for r in filed) or 'nobody'}\n"
        f"marked No Report: {', '.join(f'<@{r}>' for r in missing) or 'nobody'}\n"
        f"reminders sent: {', '.join(f'<@{r}>' for r in missing + [DEBUG_DM_USER])}"
    )
    reconcile()
