#!/usr/bin/env bash
# Stage everything git already tracks or does not ignore, commit, and push.
# No force-push. No token is read or written. git uses the login already on this Mac.
#
# Usage: scripts/git-commit-push.sh "commit message"

set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/common.sh"
cd "$(repo_root)"
refuse_credentials_in_origin

if [[ $# -lt 1 || "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  echo 'Usage: scripts/git-commit-push.sh "commit message"' >&2
  exit 2
fi

message="$*"
if [[ -z "${message//[[:space:]]/}" ]]; then
  echo "Commit message is empty." >&2
  exit 2
fi

branch=$(git rev-parse --abbrev-ref HEAD)
if [[ "$branch" == "HEAD" ]]; then
  echo "This checkout is detached. Check out a branch, then run this again." >&2
  exit 1
fi

git add -A
if git diff --cached --quiet; then
  echo "Nothing to commit." >&2
  exit 1
fi

git diff --cached --stat
git commit -m "$message"

if git rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
  git push
else
  git push -u origin "$branch"
fi

echo "Pushed $branch to origin."
