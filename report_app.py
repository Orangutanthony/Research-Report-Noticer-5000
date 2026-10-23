import os
import runpy
import signal
import sys
import threading
import time
import urllib.parse
from http.server import ThreadingHTTPServer
from pathlib import Path

PORT = 8780
SERVER = "https://haagbot.lol"
# while the settings window is open the server keeps the browser page locked, this often
PRESENCE_SECONDS = 10
STATUS_SECONDS = 20


def data_dir():
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home())
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "report-bot-app"


def chosen_data():
    # the server names its folder with --data, the app on a computer uses the user's own folder
    if "--data" not in sys.argv:
        return os.environ.get("REPORT_BOT_DATA") or str(data_dir())
    at = sys.argv.index("--data")
    if at + 1 >= len(sys.argv):
        raise SystemExit("--data needs a folder")
    folder = sys.argv[at + 1]
    del sys.argv[at:at + 2]
    return str(Path(folder).resolve())


# set before the bot's modules are imported, and inherited by every team's bot this starts
os.environ["REPORT_BOT_DATA"] = chosen_data()

import invite  # noqa: E402
import setup_server  # noqa: E402
import supervisor  # noqa: E402
import teams  # noqa: E402

LOCAL = f"http://127.0.0.1:{PORT}/setup"


class LocalServer(ThreadingHTTPServer):
    # windows lets a second program bind a reused port, which would hide that the app is already running
    allow_reuse_address = sys.platform != "win32"


def launch():
    # the command that starts this program again, frozen or run from source
    return [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, str(Path(__file__).resolve())]


def connected_team():
    return setup_server.deployment.get("LOCAL_TEAM")


def open_link():
    # a fresh one-time link each time the window opens
    team = connected_team()
    if not team:
        return f"{LOCAL}/connect"
    return f"{LOCAL}/{teams.create_invite(team, setup_server.deployment['ADMIN_USER'])}"


def connect(key, server):
    if key:
        server = server or setup_server.deployment.get("GATEWAY_URL") or SERVER
        if urllib.parse.urlsplit(server).scheme not in ("http", "https"):
            raise setup_server.Problem("open the connect link from your team's setup page")
        teams.write_env(teams.DEPLOYMENT_ENV, {**setup_server.deployment, "GATEWAY_URL": server, "GATEWAY_KEY": key.strip()})
        setup_server.reload()
        try:
            greeting = setup_server.hello()
        except setup_server.Problem:
            teams.write_env(teams.DEPLOYMENT_ENV, {k: v for k, v in setup_server.deployment.items() if k != "GATEWAY_KEY"})
            setup_server.reload()
            raise
        teams.write_env(teams.DEPLOYMENT_ENV, {
            **setup_server.deployment, "LOCAL_TEAM": greeting["team"],
            "ADMIN_USER": greeting["user"]["id"], "BOT_USER_ID": greeting["bot_user_id"],
        })
        setup_server.reload()
        teams.folder(greeting["team"]).mkdir(parents=True, exist_ok=True)
        if window["open"]:
            # the server only knows the new key from now on, so the open window is announced again with it
            presence(True)
    return open_link()


window = {"open": False}


def presence(opened):
    window["open"] = opened
    if setup_server.deployment.get("GATEWAY_KEY"):
        try:
            setup_server.gateway("presence", {"open": opened})
        except setup_server.Problem:
            pass


def keep_presence():
    while True:
        time.sleep(PRESENCE_SECONDS)
        if window["open"]:
            presence(True)


def bot_command(name):
    return launch() + ["--bot", "--team", name]


def supervise(boss, before_tick=lambda: None):
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        while True:
            before_tick()
            boss.tick()
            time.sleep(supervisor.TICK_SECONDS)
    finally:
        boss.stop_all()


def run_desktop():
    # started hidden by the desktop app, which owns the window, the tray and the report-bot-app link
    try:
        httpd = LocalServer(("127.0.0.1", PORT), setup_server.Handler)
    except OSError:
        raise SystemExit(f"port {PORT} is taken, the app may already be running")
    setup_server.on_connect = connect
    setup_server.on_window = presence
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    threading.Thread(target=keep_presence, daemon=True).start()
    print(f"report bot app on {LOCAL}/", flush=True)
    deployment = setup_server.deployment
    wanted = {"here": False, "checked": 0.0}
    boss = supervisor.Supervisor(None, None, command=bot_command,
                                 names=lambda: [t for t in [connected_team()] if t and teams.env_path(t).is_file() and wanted["here"]])

    def follow_connection():
        # crash alerts go to the team's admin through the gateway since there is no bot token here
        boss.token, boss.operator = deployment.get("GATEWAY_KEY"), deployment.get("ADMIN_USER")
        boss.base = f"{deployment.get('GATEWAY_URL', '').rstrip('/')}/setup/gateway/slack"
        # the bot runs here only while the server says this computer has the team and it is not stopped
        if connected_team() and time.monotonic() - wanted["checked"] > STATUS_SECONDS:
            wanted["checked"] = time.monotonic()
            try:
                found = setup_server.gateway("status")
                wanted["here"] = found["location"] == "computer" and not found["paused"]
            except setup_server.Problem:
                pass
    supervise(boss, follow_connection)


def run_server():
    deployment = setup_server.deployment
    port = int(deployment["SETUP_PORT"])
    threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", port), setup_server.Handler).serve_forever, daemon=True).start()
    print(f"report bot server: setup pages and gateway on 127.0.0.1:{port}, teams in {teams.TEAMS_DIR}", flush=True)
    threading.Thread(target=setup_server.backfill, daemon=True).start()
    supervise(supervisor.Supervisor(deployment["SLACK_BOT_TOKEN"], deployment["OPERATOR_USER"], command=bot_command,
                                    names=lambda: [t for t in teams.names() if not teams.paused(t)]))


def team_flag(args, usage):
    if "--team" not in args or args.index("--team") + 1 >= len(args):
        raise SystemExit(usage)
    return args[args.index("--team") + 1]


USAGE = """usage:
  report-bot-app --desktop                  run for the desktop app, which starts it
  report-bot-app --server --data <folder>   run the server
  report-bot-app invite <slack user id> --team <team name> --data <server folder>
  report-bot-app revoke --team <team name> --data <server folder>"""


def main():
    args = sys.argv[1:]
    if args[:1] == ["--bot"]:
        sys.argv = [sys.argv[0]] + args[1:]
        runpy.run_module("app", run_name="__main__")
        return
    if args[:1] == ["--desktop"]:
        return run_desktop()
    if args[:1] == ["--server"]:
        return run_server()
    if args[:1] == ["invite"]:
        team = team_flag(args, USAGE)
        users = [a for a in args[1:] if a not in ("--team", team)]
        if len(users) != 1:
            raise SystemExit(USAGE)
        return print(invite.send(users[0], team))
    if args[:1] == ["revoke"]:
        return print(invite.revoke(team_flag(args, USAGE)))
    raise SystemExit(USAGE)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
