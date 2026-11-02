"""Start work on a ticket in its own git worktree, so the checkout you are in stays as it is."""
import os
import re
import subprocess

from . import notion as nt


class BranchError(Exception):
    pass


def git(args, runner=None):
    """Run git and return stdout; raise BranchError with git's own message on failure."""
    try:
        r = (runner or subprocess.run)(["git"] + args, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise BranchError("git: %s" % e)
    if r.returncode != 0:
        raise BranchError((r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout).strip() else "git failed")
    return r.stdout.strip()


def slug(text, limit=40):
    """'Fix: login redirect (staging)' -> 'fix-login-redirect-staging'."""
    s = re.sub(r"[^a-z0-9]+", "-", nt.clean(text).lower()).strip("-")
    return s[:limit].rstrip("-")


def branch_name(page, prefix=""):
    """The ticket id and title as a branch name: et-2546-fix-login-redirect (or just the title)."""
    tid = nt.page_ticket_id(page).lower()
    return prefix + "-".join(x for x in (tid, slug(nt.page_title(page), 40 if not tid else 32)) if x) or prefix + "ticket"


def repo_root(cwd, runner=None):
    """The git repository containing `cwd`, or '' when it is not inside one."""
    try:
        return git(["-C", cwd, "rev-parse", "--show-toplevel"], runner)
    except BranchError:
        return ""


def base_ref(root, runner=None):
    """Where the new branch starts: the local main or master, otherwise the current commit."""
    for name in ("main", "master"):
        try:
            git(["-C", root, "rev-parse", "--verify", "--quiet", "refs/heads/" + name], runner)
            return name
        except BranchError:
            continue
    return "HEAD"


def worktree_path(root, branch, exists=os.path.exists):
    """A sibling directory of the repo named after the branch; numbered when it is taken."""
    base = "%s-%s" % (root.rstrip("/"), slug(branch.replace("/", "-"), 50))
    path, n = base, 2
    while exists(path):
        path, n = "%s-%d" % (base, n), n + 1
    return path


def create(root, branch, path, base, runner=None):
    """git worktree add -b BRANCH PATH BASE. Raises BranchError (for example when the branch already exists)."""
    git(["-C", root, "worktree", "add", "-b", branch, path, base], runner)
    return path
