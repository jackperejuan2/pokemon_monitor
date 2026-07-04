#!/usr/bin/env bash
# One-time setup: create an orphan gh-pages branch, a worktree for it under
# ~/.pokemon-monitor/pages, and enable GitHub Pages on that branch.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
WORKTREE="$HOME/.pokemon-monitor/pages"
mkdir -p "$(dirname "$WORKTREE")"

cd "$REPO_ROOT"

if ! git show-ref --verify --quiet refs/heads/gh-pages; then
  echo "creating orphan gh-pages branch"
  rm -rf "$WORKTREE"
  git worktree add --detach "$WORKTREE" HEAD
  ( cd "$WORKTREE"
    git checkout --orphan gh-pages
    git rm -rf . >/dev/null 2>&1 || true
    echo '<!DOCTYPE html><html><body>Pokemon monitor dashboard — warming up…</body></html>' > index.html
    git add index.html
    git commit -m "seed gh-pages"
    git push -u origin gh-pages )
else
  echo "gh-pages branch already exists"
  if [ ! -d "$WORKTREE" ]; then
    git worktree add "$WORKTREE" gh-pages
  fi
fi

OWNER_REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner)"
echo '{"source":{"branch":"gh-pages","path":"/"}}' \
  | gh api -X POST "repos/$OWNER_REPO/pages" --input - 2>/dev/null \
  || echo "Pages may already be enabled (or enable it in Settings -> Pages -> gh-pages / root)"

echo "Done. Worktree: $WORKTREE"
echo "Page will be at: https://$(echo "$OWNER_REPO" | cut -d/ -f1).github.io/$(echo "$OWNER_REPO" | cut -d/ -f2)/"
