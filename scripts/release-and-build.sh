#!/usr/bin/env bash
# Create the GitHub release for a tag that is already on origin, then start
# Build Windows and Linux on that tag. gh uses the login from `gh auth login`.
# No token is stored in this file.
#
# The workflow file is .github/workflows/build-windows-linux.yml.
# Pushing ALPHA_* already starts it. This script leaves that run alone.
# Pushing BETA_* does not start it, so this script starts it with --ref.
# The publish job attaches archives only when the run ref is a tag:
#   MonitorMaid-<tag>-windows-amd64.zip
#   MonitorMaid-<tag>-linux-x86_64.tar.gz
#
# Usage: scripts/release-and-build.sh TAG [--again]
#   --again   start another run even if one is already queued or running

set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/common.sh"
cd "$(repo_root)"
refuse_credentials_in_origin

workflow_file="build-windows-linux.yml"
notes="Windows and Linux builds. Unzip or untar and run monitor-maid. Ollama is installed separately. Python is bundled."

usage() {
  echo "Usage: scripts/release-and-build.sh TAG [--again]" >&2
  exit 2
}

tag=""
again=0
for arg in "$@"; do
  case "$arg" in
    -h|--help) usage ;;
    --again) again=1 ;;
    *)
      if [[ -n "$tag" ]]; then
        usage
      fi
      tag="$arg"
      ;;
  esac
done
if [[ -z "$tag" ]]; then
  usage
fi
require_tag_name "$tag"

if ! command -v gh >/dev/null 2>&1; then
  echo "gh is not on PATH. Install GitHub CLI and run gh auth login." >&2
  exit 1
fi
gh auth status

remote_sha=$(remote_tag_sha "$tag")
if [[ -z "$remote_sha" ]]; then
  echo "Tag $tag is not on origin. Run scripts/tag-origin.sh $tag first." >&2
  exit 1
fi

if git show-ref --verify --quiet "refs/tags/$tag"; then
  local_sha=$(git rev-parse "refs/tags/$tag")
  if [[ "$local_sha" != "$remote_sha" ]]; then
    echo "Local tag $tag differs from origin. This script does not overwrite either one." >&2
    exit 1
  fi
else
  git fetch origin "refs/tags/${tag}:refs/tags/${tag}"
fi

if ! git cat-file -e "${tag}:.github/workflows/${workflow_file}" 2>/dev/null; then
  echo "Tag $tag does not contain .github/workflows/${workflow_file}." >&2
  echo "The build uses the workflow stored on the tag. Tag a commit that has it." >&2
  exit 1
fi

if gh release view "$tag" >/dev/null 2>&1; then
  echo "Release $tag already exists."
else
  # --verify-tag refuses to invent a tag on the default branch.
  # A tag push may create the same release while this runs. That is success.
  if gh release create "$tag" \
    --verify-tag \
    --title "Monitor Maid ${tag}" \
    --notes "$notes"
  then
    echo "Created release $tag."
  elif gh release view "$tag" >/dev/null 2>&1; then
    echo "Release $tag already exists."
  else
    echo "Could not create release $tag." >&2
    exit 1
  fi
fi

active_runs() {
  gh run list \
    --workflow "$workflow_file" \
    --limit 40 \
    --json databaseId,event,headBranch,status,url \
    --jq ".[] | select(.headBranch == \"$tag\") | select(.status == \"queued\" or .status == \"in_progress\" or .status == \"requested\" or .status == \"waiting\" or .status == \"pending\" or .status == \"action_required\") | \"\(.databaseId)\t\(.event)\t\(.status)\t\(.url)\""
}

active=$(active_runs)
if [[ -n "$active" && "$again" -eq 0 ]]; then
  echo "A build for $tag is already queued or running:"
  printf '%s\n' "$active"
  echo "Left that run in place. Pass --again to start another."
  exit 0
fi

echo "Starting ${workflow_file} on tag $tag."
gh workflow run "$workflow_file" --ref "$tag"

url=""
for _ in 1 2 3 4 5 6 7 8; do
  sleep 2
  url=$(active_runs | awk -F '\t' 'NR == 1 { print $4 }')
  if [[ -n "$url" ]]; then
    break
  fi
done

if [[ -n "$url" ]]; then
  echo "Workflow run: $url"
else
  echo "The dispatch was accepted. The run was not listed yet."
  echo "Check it with: gh run list --workflow ${workflow_file} --limit 5"
fi
echo "When the run is on the tag, it attaches MonitorMaid-${tag}-windows-amd64.zip and MonitorMaid-${tag}-linux-x86_64.tar.gz."
