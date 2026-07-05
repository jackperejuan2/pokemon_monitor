# publisher.py
"""Publish the rendered dashboard to a gh-pages branch via a dedicated git
worktree. publish() never raises: a failed push must never disrupt monitoring."""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

log = logging.getLogger("publisher")

PAGES_WORKTREE = Path.home() / ".pokemon-monitor" / "pages"


def should_publish(dirty: bool, is_heartbeat: bool,
                   minutes_since_publish: float = 0.0,
                   max_stale_minutes: float = 30.0) -> bool:
    return bool(dirty or is_heartbeat or minutes_since_publish >= max_stale_minutes)


def publish(html: str, worktree: Path = PAGES_WORKTREE, runner=subprocess.run) -> bool:
    """Write index.html into the gh-pages worktree, amend the single rolling
    commit, and force-push. Returns True on success, False (logged) on any error.
    The worktree must already exist (scripts/setup-pages.sh). render_html embeds a
    timestamp, so there is always a diff to commit."""
    try:
        worktree = Path(worktree)
        if not worktree.exists():
            log.warning("pages worktree %s missing; run scripts/setup-pages.sh", worktree)
            return False
        (worktree / "index.html").write_text(html)
        git = ["git", "-C", str(worktree)]
        runner(git + ["add", "-A"], check=True)
        runner(git + ["commit", "--amend", "--no-edit", "--allow-empty"], check=True)
        runner(git + ["push", "--force", "origin", "gh-pages"], check=True)
        return True
    except Exception:
        log.exception("dashboard publish failed")
        return False
