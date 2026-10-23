import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

if os.name == "nt":
    import winreg

TIMEOUT = 600
COMMANDS = {"claude": "claude", "codex": "codex", "antigravity": "agy"}
WINDOWS_PATHS = [("HKEY_CURRENT_USER", "Environment"),
                 ("HKEY_LOCAL_MACHINE", r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")]


def search_path():
    # services start with a bare PATH that misses per-user installs, and on windows a cli installed
    # after this program started is only on the path the registry holds
    paths = [os.environ.get("PATH", ""), str(Path.home() / ".local" / "bin")]
    if os.name == "nt":
        for root, key in WINDOWS_PATHS:
            try:
                with winreg.OpenKey(getattr(winreg, root), key) as found:
                    paths.append(os.path.expandvars(winreg.QueryValueEx(found, "Path")[0]))
            except OSError:
                pass
    return os.pathsep.join(paths)


def cli(name):
    found = shutil.which(name, path=search_path())
    if not found:
        raise RuntimeError(f"{name} is not installed for this user")
    return found


def installed():
    path = search_path()
    return {provider: bool(shutil.which(name, path=path)) for provider, name in COMMANDS.items()}


def with_document(prompt, text):
    return f"{prompt}\n\n--- NEW DOCUMENT ---\n{text}\n--- END OF NEW DOCUMENT ---"


def run(args, **kw):
    # an empty working folder so the cli has nothing of the user's to look through
    with tempfile.TemporaryDirectory() as empty:
        return subprocess.run(args, cwd=empty, capture_output=True, text=True, encoding="utf-8", timeout=TIMEOUT,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), **kw)


SLUG = re.compile(r"[a-z0-9][a-z0-9.\-]*[a-z0-9]")


def claude(prompt, text, model=None):
    pick = ["--model", model] if model else []
    result = run(
        [cli("claude"), "-p", *pick, "--output-format", "json", "--tools", "", "--no-session-persistence", prompt],
        input=text,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude exited {result.returncode}: {result.stderr[:300]}")
    envelope = json.loads(result.stdout)
    if envelope.get("is_error"):
        raise RuntimeError(f"claude error: {str(envelope.get('result'))[:300]}")
    return envelope["result"], f"claude ran {', '.join(envelope.get('modelUsage') or {}) or 'an unreported model'}"


def codex(prompt, text, model=None):
    pick = ["-m", model] if model else []
    result = run([cli("codex"), "exec", *pick, "--skip-git-repo-check", "--ephemeral", "-"], input=with_document(prompt, text))
    if result.returncode != 0:
        raise RuntimeError(f"codex exited {result.returncode}: {result.stderr[-300:]}")
    ran = re.search(r"^model: *(\S+)", result.stderr, re.M)
    return result.stdout, f"codex ran {ran.group(1) if ran else 'an unreported model'}"


def antigravity(prompt, text, model=None):
    # agy only takes the prompt as an argument and hangs on an open stdin before 1.1.1
    pick = ["--model", model] if model else []
    result = run([cli("agy"), "-p", with_document(prompt, text), *pick, "--output-format", "json"], stdin=subprocess.DEVNULL)
    if result.returncode != 0:
        raise RuntimeError(f"agy exited {result.returncode}: {result.stderr[-300:]}")
    if not result.stdout.strip():
        raise RuntimeError("agy printed nothing, versions before 1.1.1 drop output when not in a terminal")
    reply = json.loads(result.stdout)
    if reply.get("error"):
        raise RuntimeError(f"agy error: {str(reply['error'])[:300]}")
    return reply["response"], None


def local(prompt, text, url, model, key=None, temperature=0.0, max_tokens=None, timeout=TIMEOUT):
    if not url or not model:
        raise RuntimeError("the local model needs a server address and a model name")
    body = {"model": model, "temperature": temperature, "messages": [{"role": "user", "content": with_document(prompt, text)}]}
    if max_tokens:
        body["max_tokens"] = max_tokens
    headers = {"Content-Type": "application/json", **({"Authorization": f"Bearer {key}"} if key else {})}
    request = urllib.request.Request(f"{url.rstrip('/')}/v1/chat/completions", json.dumps(body).encode(), headers)
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        reply = json.load(resp)
    return reply["choices"][0]["message"]["content"], f"local model {reply.get('model') or model}"


def models(provider):
    # what this computer's own cli says it can run, so the choice matches the login it will use
    # claude code has no way to list its models, so it only takes a typed name
    try:
        if provider == "codex":
            result = run([cli("codex"), "debug", "models"], stdin=subprocess.DEVNULL)
            listed = [m for m in json.loads(result.stdout).get("models", []) if m.get("visibility") == "list"]
            return [(m["slug"], m.get("display_name") or m["slug"]) for m in sorted(listed, key=lambda m: m.get("priority", 99))]
        if provider == "antigravity":
            result = run([cli("agy"), "models"], stdin=subprocess.DEVNULL)
            # each line is a slug, then a tab and its display name
            found = [(SLUG.search(line), line.partition("	")[2].strip()) for line in result.stdout.splitlines()]
            return [(m.group(0), name or m.group(0)) for m, name in found if m and "-" in m.group(0)]
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
        return []
    return []


def local_models(url, key=None):
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        with urllib.request.urlopen(urllib.request.Request(f"{url.rstrip('/')}/v1/models", headers=headers), timeout=5) as resp:
            return [(m["id"], m["id"]) for m in json.load(resp).get("data", [])]
    except (OSError, ValueError, KeyError):
        return []
