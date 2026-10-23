import logging
import subprocess
import sys
import time

import slack_http
import stats
import teams

APP = teams.ROOT / "app.py"
TICK_SECONDS = 5
STAGGER_SECONDS = 10
MAX_BACKOFF_SECONDS = 300
CRASH_WINDOW_SECONDS = 600
CRASHES_BEFORE_ALERT = 3

logging.basicConfig(level=logging.INFO, format="%(levelname)s [supervisor] %(message)s")
log = logging.getLogger("supervisor")


class Worker:
    def __init__(self, name):
        self.name = name
        self.proc = None
        self.mtime = None
        self.crashes = []
        self.start_at = 0.0
        self.alerted = False


def app_command(name):
    return [sys.executable, str(APP), "--team", name]


class Supervisor:
    def __init__(self, token, operator, base=None, command=app_command, names=teams.names):
        self.token = token
        self.operator = operator
        self.base = base
        self.command = command
        self.names = names
        self.workers = {}
        self.next_slot = 0.0

    def alert(self, text):
        log.warning(text)
        try:
            slack_http.post_json(self.token, "chat.postMessage", {"channel": self.operator, "text": text}, self.base)
        except Exception:
            log.exception("could not alert the operator")

    def schedule(self, worker, delay=0.0):
        now = time.monotonic()
        if delay:
            worker.start_at = now + delay
            return
        # starts are spread out so a restart of every team does not burst the rate limits
        worker.start_at = max(now, self.next_slot)
        self.next_slot = worker.start_at + STAGGER_SECONDS

    def start(self, worker):
        worker.mtime = teams.env_path(worker.name).stat().st_mtime
        # no console window for a bot started by the desktop app on windows
        worker.proc = subprocess.Popen(self.command(worker.name), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        log.info("started %s, pid %s", worker.name, worker.proc.pid)
        stats.mark_started(worker.name)

    def stop(self, worker, why):
        if worker.proc and worker.proc.poll() is None:
            log.info("stopping %s, %s", worker.name, why)
            worker.proc.terminate()
            try:
                worker.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                worker.proc.kill()
        worker.proc = None
        stats.mark_stopped(worker.name)

    def exited(self, worker, code):
        now = time.monotonic()
        worker.crashes = [t for t in worker.crashes if now - t < CRASH_WINDOW_SECONDS] + [now]
        backoff = min(5 * 2 ** (len(worker.crashes) - 1), MAX_BACKOFF_SECONDS)
        log.warning("%s exited with %s, restarting in %ss", worker.name, code, backoff)
        worker.proc = None
        stats.mark_stopped(worker.name)
        self.schedule(worker, backoff)
        if len(worker.crashes) >= CRASHES_BEFORE_ALERT and not worker.alerted:
            worker.alerted = True
            self.alert(f"*team {worker.name} keeps crashing*: {len(worker.crashes)} exits in"
                       f" {CRASH_WINDOW_SECONDS // 60} minutes, last exit code {code}. Check its output.")

    def tick(self):
        now = time.monotonic()
        wanted = set(self.names())
        for name in sorted(wanted - set(self.workers)):
            self.workers[name] = Worker(name)
            self.schedule(self.workers[name])
        for name in sorted(set(self.workers) - wanted):
            self.stop(self.workers.pop(name), "its team.env is gone")
        for worker in self.workers.values():
            if worker.proc is None:
                if now >= worker.start_at:
                    self.start(worker)
                continue
            code = worker.proc.poll()
            if code is not None:
                self.exited(worker, code)
            elif teams.env_path(worker.name).stat().st_mtime != worker.mtime:
                self.stop(worker, "its team.env changed")
                worker.crashes, worker.alerted = [], False
                self.schedule(worker)
            elif worker.crashes and now - worker.crashes[-1] > CRASH_WINDOW_SECONDS:
                worker.crashes, worker.alerted = [], False

    def stop_all(self):
        for worker in self.workers.values():
            self.stop(worker, "supervisor shutting down")

