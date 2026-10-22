import store
from config import CHANNEL, REACT_EMOJI, REACT_ENABLED, REACT_FROM_TS
from slack_api import form


def react(msg, week, slack_file):
    if not REACT_ENABLED:
        return "reactions disabled"
    # the cutoff keeps a cursor rewind from reacting to old posts
    if float(msg["ts"]) < REACT_FROM_TS:
        return "older than the reaction cutoff, not reacting"
    try:
        form("reactions.add", {"channel": CHANNEL, "timestamp": msg["ts"], "name": REACT_EMOJI})
    except RuntimeError as error:
        if "already_reacted" in str(error):
            return "already reacted"
        raise
    store.record(
        "reacted", {"ts": msg["ts"], "emoji": REACT_EMOJI, "file_id": slack_file["id"]},
        researcher=msg["user"], week=week,
    )
    return f"reacted :{REACT_EMOJI}:"
