import json
import re
from datetime import date
from pathlib import Path

import runners
from config import EXAMPLES_DIR, LINKS_RULES, NO_REPORT, NOT_APPLICABLE, REPORT_DEFINITION, STATUS_OPTIONS, SUMMARY_STYLE, env
from processors import Classification, Links, Summary, Week, github, links
from processors.text import file_text, posted_text

ATTEMPTS = 3
EXAMPLE_CHARS = 8000
MAX_BULLETS = 4
MAX_BULLET_CHARS = 300
MAX_LINKS = 4
PR_BODY_CHARS = 700
PROVIDERS = {"claude": "Claude Code CLI", "codex": "Codex CLI", "antigravity": "Gemini (Antigravity CLI)", "local": "Local LLM"}
DEFAULTS = Path(__file__).parent


def described(custom, default):
    # a team's own wording, edited on the setup page, replaces the default
    return (custom if custom.is_file() else DEFAULTS / default).read_text(encoding="utf-8")


def examples(uid):
    folder = EXAMPLES_DIR / uid
    texts = (file_text(path) for path in sorted(folder.iterdir()) if path.is_file()) if folder.is_dir() else ()
    return [text[:EXAMPLE_CHARS] for text in texts if text and text.strip()]


def report_prompt(samples):
    if samples:
        past = "\n\n".join(f"--- PAST REPORT BY THIS RESEARCHER {i} ---\n{s}" for i, s in enumerate(samples, 1))
        grounding = f"Past weekly reports by this same researcher:\n\n{past}\n\n--- END OF PAST REPORTS ---"
    else:
        grounding = "No past reports by this researcher are on file."
    return f"""{described(REPORT_DEFINITION, "report-definition.md")}

{grounding}

You are now given the extracted text of a new document posted by this researcher.

Based on the past reports above and the explanation of how HAAG weekly reports are formatted, is this
an official, complete weekly report, or some other document sent by the researcher?

Output ONLY a JSON object, no prose and no code fences:

{{"is_report": true or false}}

A genuine weekly report almost always states a week number or a date, usually both. If it states
NEITHER, that is strong evidence it is not a weekly report. In that case only answer true if you are
VERY sure it is one anyway."""


def statuses():
    return [s for s in STATUS_OPTIONS if s != NO_REPORT]


def summary_prompt():
    return f"""{described(SUMMARY_STYLE, "summary-style.md")}

You are given the extracted text of a weekly report by one researcher. Work out which week it covers
and summarize it.

Output ONLY a JSON object, no prose and no code fences:

{{
  "week_number": integer or null,
  "date_source": string or null,
  "report_date": "YYYY-MM-DD" or null,
  "status": {" | ".join(repr(s) for s in statuses())},
  "summary": [string, ...],
  "blockers": [string, ...]
}}

week_number: the week the report covers, worked out as described above.
date_source: the date as written in the document, copied exactly character for character. If the
document states no date, use null. Never infer or invent a date.
report_date: date_source converted to YYYY-MM-DD, or null when date_source is null.
status: the status most consistent with the report as a whole.
summary: the progress made this week. Blockers do not belong here.
blockers: anything blocking the work. Most weeks have none, in which case return []."""


MATCH_PROMPT = """Below are the summary bullets of one researcher's weekly report, then the pull requests and links
that might be the work those bullets describe.

Summary bullets:
{bullets}

Pull requests this researcher opened around this week:
{prs}

GitHub links in the report and its Slack message:
{doclinks}

{rules}

Reply with only a JSON object, no other text:

{{"verdicts": [{{"url": "...", "qualifies": true or false, "bullet": "the matching bullet, or null", "why": "one short sentence"}}]}}

Give one verdict per candidate. Copy each URL exactly as listed above."""


def retry_prompt(base, problems):
    return base + "\n\nYour previous answer broke these rules. Fix them:\n" + "\n".join(f"- {p}" for p in problems)


def parse(reply):
    # local reasoning models print their thinking before the answer
    body = re.sub(r"<think>.*?</think>", "", reply, flags=re.S)
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        answer = json.loads(body[start:end + 1])
    except ValueError:
        return None
    return answer if isinstance(answer, dict) else None


def report_problems(answer, text):
    if answer is None:
        return ["the answer must be a single JSON object"]
    return [] if isinstance(answer.get("is_report"), bool) else ["is_report must be true or false"]


def strip_bullet(text):
    return re.sub(r"^[\s\u2022\-*\u00b7]+", "", text).strip()


def bullet_problems(field, bullets):
    problems = []
    for bullet in bullets:
        if len(bullet) > MAX_BULLET_CHARS:
            problems.append(f"{field} bullet is {len(bullet)} chars, the limit is {MAX_BULLET_CHARS}: {bullet[:60]}...")
        nonascii = sorted({c for c in bullet if ord(c) > 127})
        if nonascii:
            problems.append(f"{field} bullet contains non-ascii {nonascii}, use plain ascii only: {bullet[:60]}...")
        if ";" in bullet:
            problems.append(f"{field} bullet contains a semicolon: {bullet[:60]}...")
    return problems


def summary_problems(answer, text):
    if answer is None:
        return ["the answer must be a single JSON object"]
    problems = []
    week = answer.get("week_number")
    if week is not None and (type(week) is not int or week < 1):
        problems.append("week_number must be a positive integer or null")
    if answer.get("status") not in statuses():
        problems.append(f"status must be one of {tuple(statuses())}")
    source = answer.get("date_source")
    if source is None:
        if answer.get("report_date") is not None:
            problems.append("report_date must be null when date_source is null, never infer a date")
    elif not isinstance(source, str) or source not in text:
        problems.append(f"date_source {source!r} does not appear in the document, copy it exactly or use null")
    else:
        try:
            date.fromisoformat(answer.get("report_date"))
        except (TypeError, ValueError):
            problems.append("report_date must be a YYYY-MM-DD string")
    for field in ("summary", "blockers"):
        bullets = answer.get(field)
        if not isinstance(bullets, list) or not all(isinstance(b, str) for b in bullets):
            problems.append(f"{field} must be a list of strings")
            continue
        bullets = [b for b in (strip_bullet(b) for b in bullets) if b]
        answer[field] = bullets
        if len(bullets) > MAX_BULLETS:
            problems.append(f"{field} has {len(bullets)} bullets, the limit is {MAX_BULLETS}")
        problems += bullet_problems(field, bullets)
    return problems


def answer_until_valid(ask, base, text, problems_in, notes):
    # ask(prompt, document text) returns the model's reply and a note naming the model that ran
    prompt = base
    for _ in range(ATTEMPTS):
        reply, ran = ask(prompt, text)
        answer = parse(reply)
        problems = problems_in(answer, text)
        if ran:
            notes.append(ran)
        if not problems:
            return answer
        notes.append(f"retried, previous answer broke: {'; '.join(problems)[:280]}")
        prompt = retry_prompt(base, problems)
    raise RuntimeError(f"no valid answer after {ATTEMPTS} attempts: {'; '.join(problems)}")


def skipped(doc):
    return Classification(False, notes=(f"skipped, {doc.mimetype or 'unknown type'} is not a pdf or docx",))


def decide(doc, ask):
    text = posted_text(doc)
    if text is None:
        return skipped(doc)
    samples = examples(doc.poster)
    notes = [f"extracted {len(text):,} chars, classifying against {len(samples)} example reports"]
    answer = answer_until_valid(ask, report_prompt(samples), text, report_problems, notes)
    return Classification(answer["is_report"], tuple(notes))


def write_summary(doc, ask):
    text = posted_text(doc)
    if text is None:
        raise RuntimeError(f"nothing to summarize, {doc.mimetype or 'unknown type'} is not a pdf or docx")
    notes = [f"summarizing {len(text):,} chars"]
    answer = answer_until_valid(ask, summary_prompt(), text, summary_problems, notes)
    stated = date.fromisoformat(answer["report_date"]) if answer.get("date_source") is not None else None
    week = Week(answer.get("week_number"), stated, (f"week {answer.get('week_number')}, date {answer.get('date_source')!r} as written",))
    # an empty list means checked and nothing applies
    return Summary(
        week, answer["status"],
        tuple(answer["summary"]) or NOT_APPLICABLE, tuple(answer["blockers"]) or NOT_APPLICABLE,
        tuple(notes),
    )


def verdict_problems(answer, text):
    if answer is None:
        return ["the answer must be a single JSON object"]
    verdicts = answer.get("verdicts")
    if not isinstance(verdicts, list) or not all(isinstance(v, dict) for v in verdicts):
        return ["verdicts must be a list of objects"]
    return []


def pick_links(doc, summary, week, ask):
    if not isinstance(summary.summary, tuple):
        return Links(notes=("no summary bullets to match work against, Link to Work left unset",))
    notes = []
    text = posted_text(doc) or ""
    prs = github.for_week(doc.poster, week, notes)
    found = links.candidates(doc.read(), doc.mimetype, text, doc.message)
    if not prs and not found:
        notes.append("no pr or document link candidates, Link to Work is N/A")
        return Links(NOT_APPLICABLE, tuple(notes))
    prompt = MATCH_PROMPT.format(
        bullets="\n".join(f"- {b}" for b in summary.summary),
        prs=github.describe(prs, PR_BODY_CHARS),
        doclinks=links.describe(found) or "- (none found in this document)",
        rules=described(LINKS_RULES, "links-rules.md").strip(),
    )
    verdicts = answer_until_valid(ask, prompt, text, verdict_problems, notes)["verdicts"]
    allowed = [row["url"] for row in prs] + list(found)
    picked, invented, lines = [], [], []
    for verdict in verdicts:
        url = verdict.get("url")
        if not verdict.get("qualifies"):
            lines.append(f"rejected {url}\n          {verdict.get('why')}")
            continue
        # a url outside the candidate set was invented, so it is dropped rather than retried
        if url not in allowed:
            invented.append(str(url))
        elif url not in picked:
            picked.append(url)
            lines.append(f"adopted {url}\n          {verdict.get('why')}")
    picked.sort(key=allowed.index)
    notes.append("*link matching*\n" + "\n".join(lines))
    if invented:
        notes.append(f"*dropped, not in the candidate set*: {', '.join(invented)}")
    return Links(tuple(picked[:MAX_LINKS]) or NOT_APPLICABLE, tuple(notes))


def number(key, kind, default):
    return kind(env[key]) if env.get(key) else default


def asker(prefix):
    # a step's provider and model come from its own settings, a local model's server is shared by every step
    provider, model = env.get(f"{prefix}_PROVIDER"), env.get(f"{prefix}_MODEL") or None

    def ask(prompt, text):
        if provider not in PROVIDERS:
            raise RuntimeError(f"{prefix}_PROVIDER must be one of {', '.join(PROVIDERS)}")
        if provider != "local":
            return getattr(runners, provider)(prompt, text, model)
        if not env.get("LOCAL_LLM_URL") or not env.get("LOCAL_LLM_MODEL"):
            raise RuntimeError("set LOCAL_LLM_URL and LOCAL_LLM_MODEL to use the local model")
        return runners.local(
            prompt, text, env["LOCAL_LLM_URL"], env["LOCAL_LLM_MODEL"], env.get("LOCAL_LLM_KEY") or None,
            number("LOCAL_LLM_TEMPERATURE", float, 0.0), number("LOCAL_LLM_MAX_TOKENS", int, None),
            number("LOCAL_LLM_TIMEOUT", int, runners.TIMEOUT),
        )
    return ask
