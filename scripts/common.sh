# Shared helpers for the scripts in this directory. Source this file. Do not run it.
# These scripts use the git and gh logins already on this Mac. They store no tokens.

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  echo "Source scripts/common.sh from the other scripts. Do not run it." >&2
  exit 1
fi

repo_root() {
  local here
  here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
  git -C "$here/.." rev-parse --show-toplevel
}

# Reject an origin URL that has a token baked into it. SSH (git@github.com:...)
# is allowed. https://user:token@github.com is not.
refuse_credentials_in_origin() {
  local url
  url=$(git remote get-url origin)
  case "$url" in
    *ghp_*|*github_pat_*|*x-access-token*|*gho_*|*ghu_*|*ghs_*|*ghr_*|*"://"*":"*"@"*)
      echo "The origin URL contains credentials. These scripts do not use a token in the remote URL." >&2
      echo "Point origin at git@github.com:34Minutes/monitor-maid.git or https://github.com/34Minutes/monitor-maid.git." >&2
      exit 1
      ;;
  esac
}

# Tag names used in this repo look like ALPHA_1.0.0 and BETA_1.0.1.
require_tag_name() {
  local tag="$1"
  if [[ ! "$tag" =~ ^[A-Za-z0-9][A-Za-z0-9._+-]*$ ]]; then
    echo "Tag name must start with a letter or digit and contain only letters, digits, dot, underscore, plus, or hyphen." >&2
    exit 2
  fi
}

# Print the origin object id for refs/tags/NAME, or nothing if the tag is absent.
# Annotated tags also have a peeled refs/tags/NAME^{} line. Only the tag ref counts.
remote_tag_sha() {
  local tag="$1"
  git ls-remote --tags origin "refs/tags/${tag}" \
    | awk -v ref="refs/tags/${tag}" '$2 == ref { print $1 }'
}
