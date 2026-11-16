#!/usr/bin/env bash
# Create an annotated tag at HEAD and push that one tag to origin.
# No force. No token is stored here. Existing tags are left as they are.
#
# A tag named ALPHA_* starts .github/workflows/build-windows-linux.yml on push.
# BETA_* and any other name do not. Use scripts/release-and-build.sh for those.
#
# Usage: scripts/tag-origin.sh TAG [annotation words...]

set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/common.sh"
cd "$(repo_root)"
refuse_credentials_in_origin

if [[ $# -lt 1 || "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  echo "Usage: scripts/tag-origin.sh TAG [annotation]" >&2
  echo "Example: scripts/tag-origin.sh ALPHA_1.0.2" >&2
  exit 2
fi

tag="$1"
shift
require_tag_name "$tag"
annotation="${*:-Monitor Maid $tag}"

if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo "Tracked files have uncommitted changes. Commit them before tagging." >&2
  git status --short --untracked-files=no >&2
  exit 1
fi

if git show-ref --verify --quiet "refs/tags/$tag"; then
  echo "Tag $tag already exists in this clone. This script does not move it." >&2
  exit 1
fi

if [[ -n "$(remote_tag_sha "$tag")" ]]; then
  echo "Tag $tag already exists on origin. This script does not move it." >&2
  exit 1
fi

if [[ -n "$(git status --porcelain)" ]]; then
  echo "Untracked files stay out of the tag:"
  git status --short
fi

git tag -a "$tag" -m "$annotation"
git push origin "refs/tags/$tag"

echo "Pushed tag $tag to origin."
case "$tag" in
  ALPHA_*)
    echo "This name starts Build Windows and Linux. The workflow creates the release if it is missing and attaches the Windows zip and Linux tar.gz."
    ;;
  *)
    echo "This name does not start the Windows and Linux workflow."
    echo "Run scripts/release-and-build.sh $tag to create the release and start that workflow on this tag."
    ;;
esac
