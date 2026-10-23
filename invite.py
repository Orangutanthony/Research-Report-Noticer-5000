import slack_http
import teams


def send(user, team):
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
        "text": f"You've been invited to {verb} a weekly report bot.\n\n"
                f"{link}\n\nThis link is single-use and expires in {teams.INVITE_DAYS} days."
                f" Afterwards you can come back to {setup_page} in the same browser to change your settings any time :-)",
    })
    return f"sent {user} a link to {verb} team {team}:\n{link}"


def revoke(team):
    return f"ended {teams.revoke_sessions(team)} setup sessions for team {team}"
