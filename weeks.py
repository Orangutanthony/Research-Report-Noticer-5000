import re
from datetime import datetime, timedelta

from config import DEADLINE_HOUR, DEADLINE_TZ, WEEK_ONE_MONDAY


def posted_date(ts):
    return datetime.utcfromtimestamp(float(ts)).date()


def week_number_of(date):
    return (date - WEEK_ONE_MONDAY).days // 7 + 1


def week_covered_by(date):
    # reports are dated at the end of the week they cover so the date sits inside that week
    return week_number_of(date)


def week_label(week_number):
    monday = WEEK_ONE_MONDAY + timedelta(weeks=week_number - 1)
    friday = monday + timedelta(days=4)
    return f"Week {week_number} ({monday.month}/{monday.day} - {friday.month}/{friday.day})"


def parse_week(text):
    if not text:
        return None
    numbered = re.match(r"\s*Week\s+(\d+)\b", text)
    if numbered:
        return int(numbered.group(1))
    dated = re.match(r"\s*Week of\s+(\d{1,2})/(\d{1,2})", text)
    if dated:
        month, day = int(dated.group(1)), int(dated.group(2))
        return week_number_of(datetime(WEEK_ONE_MONDAY.year, month, day).date())
    return None


def resolve_week(classification, ts):
    stated_date = classification.report_date
    if classification.week_number:
        week = classification.week_number
        source = f"week {week} stated in document"
        if stated_date:
            implied = week_covered_by(stated_date)
            if implied != week:
                source += f", but its date {stated_date} implies week {implied}"
        return week, source
    if stated_date:
        return week_covered_by(stated_date), f"no week stated, derived from date {stated_date}"
    return week_covered_by(posted_date(ts)), "*no week and no date in document*, derived from post date"


def closed_week(now_local):
    # the week closes at the deadline hour on friday
    week = week_number_of(now_local.date())
    monday = WEEK_ONE_MONDAY + timedelta(weeks=week - 1)
    friday_close = datetime.combine(monday + timedelta(days=4), datetime.min.time(), DEADLINE_TZ).replace(hour=DEADLINE_HOUR)
    return week if now_local >= friday_close else week - 1
