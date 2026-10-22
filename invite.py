import sys

import slack_http
import teams

if sys.argv[1:2] == ["--revoke"] and sys.argv[2:3] == ["--team"] and len(sys.argv) == 4:
    print(f"ended {teams.revoke_sessions(sys.argv[3])} setup sessions for team {sys.argv[3]}")
    raise SystemExit
if len(sys.argv) != 4 or sys.argv[2] != "--team":
    raise SystemExit("usage: invite.py <slack user id> --team <team name>\n       invite.py --revoke --team <team name>")
user, team = sys.argv[1], sys.argv[3]
deployment = teams.read_env(teams.DEPLOYMENT_ENV)
setup_page = f"{deployment['SETUP_URL'].rstrip('/')}/setup/"
try:
    token = teams.create_invite(team, user)
except ValueError as error:
    raise SystemExit(str(error))
link = f"{setup_page}{token}"
verb = "change the settings of" if team in teams.names() else "set up"
slack_http.post_json(deployment["SLACK_BOT_TOKEN"], "chat.postMessage", {
    "channel": user,
    # a link preview would open the invite and use it up before the person does
    "unfurl_links": False,
    "unfurl_media": False,
    "text": f"You've been invited to {verb} the weekly report bot for team *{team}*.\n"
            f"{link}\nOpen it in the browser you'll use, it works once and expires in {teams.INVITE_DAYS} days."
            f" Afterwards you can come back to {setup_page} in that browser to change settings.",
})
print(f"sent {user} a link to {verb} team {team}:\n{link}")
