"""Delegate a page to an agent through the herdr CLI."""
import json
import os
import shlex
import subprocess
import time


class HerdrError(Exception):
    pass


def herdr_bin():
    return os.environ.get("HERDR_BIN_PATH", "herdr")


def run(args, timeout=60):
    """Run a herdr subcommand and return parsed JSON (or {} for empty output)."""
    try:
        r = subprocess.run([herdr_bin()] + args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise HerdrError("herdr %s: %s" % (args[0], e))
    if r.returncode != 0:
        raise HerdrError("herdr %s failed: %s" % (" ".join(args[:2]), (r.stderr or r.stdout).strip()[:200]))
    out = r.stdout.strip()
    if not out:
        return {}
    try:
        return json.loads(out)
    except ValueError:
        return {"raw": out}


def list_agents(runner=run):
    agents = runner(["agent", "list"]).get("result", {}).get("agents", [])
    return [{
        "pane_id": a["pane_id"],
        "name": a.get("name") or a.get("agent", ""),
        "agent": a.get("agent", ""),
        "status": a.get("agent_status", ""),
        "cwd": a.get("cwd", ""),
    } for a in agents]


def agent_label(a, home=None):
    home = home or os.path.expanduser("~")
    cwd = a["cwd"]
    if cwd.startswith(home):
        cwd = "~" + cwd[len(home):]
    return "%-8s %-14s %s" % (a["status"], (a["name"] or a["agent"])[:14], cwd)


def unique_cwds(agents, default_cwd=""):
    seen, out = set(), []
    for c in ([os.path.expanduser(default_cwd)] if default_cwd else []) + [a["cwd"] for a in agents]:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def send_prompt(pane_id, text, runner=run):
    """Deliver and submit the prompt server-side (no separate Enter)."""
    runner(["agent", "prompt", pane_id, text])


def start_agent(command, cwd, label, cfg, workspace=None, runner=run, sleep=time.sleep):
    """Create a tab in `cwd`, run the agent command in its root pane, wait until
    it is idle. Returns the pane id. Raises HerdrError if it never gets ready."""
    args = ["tab", "create", "--cwd", cwd, "--label", label, "--no-focus"]
    if workspace:
        args[2:2] = ["--workspace", workspace]
    resp = runner(args)
    pane = resp.get("result", {}).get("root_pane", {}).get("pane_id")
    if not pane:
        raise HerdrError("tab create returned no root pane")
    runner(["pane", "run", pane, " ".join(shlex.quote(c) for c in command)])
    sleep(cfg["startup_delay_ms"] / 1000.0)
    wait = cfg["wait_ready_ms"]
    if wait:
        deadline = time.time() + wait / 1000.0
        while True:  # detection can lag behind the process start
            try:
                runner(["agent", "get", pane])
                break
            except HerdrError:
                if time.time() > deadline:
                    raise HerdrError("agent not detected in time; prompt not sent")
                sleep(0.5)
        left = max(1000, int((deadline - time.time()) * 1000))
        runner(["agent", "wait", pane, "--until", "idle", "--timeout", str(left)], timeout=left // 1000 + 10)
    return pane
