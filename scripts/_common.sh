# Shared helpers for cloud-setup.sh and deploy.sh. Source, don't run.
#
#   ENV_FILE=.env.cloud   settings file (default .env.cloud)
#   DRY_RUN=1             print mutating commands instead of running them
#                         (read-only checks like `describe` still run)

set -euo pipefail

ENV_FILE="${ENV_FILE:-.env.cloud}"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE. Copy .env.cloud.example to $ENV_FILE and fill it in." >&2
  exit 1
fi
# Load KEY=VALUE lines as literal text. Deliberately not `source`: that would
# run the file as shell code and expand `$` in values such as passwords.
# Handles "double" / 'single' / unquoted values and trailing # comments.
load_env_file() {
  local line key value
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
    [[ "$line" == *=* ]] || continue
    key="${line%%=*}"
    key="${key#export }"
    key="${key//[[:space:]]/}"
    value="${line#*=}"
    value="${value#"${value%%[![:space:]]*}"}"   # trim leading spaces
    if [[ "$value" =~ ^\"(.*)\"[[:space:]]*(#.*)?$ ]]; then
      value="${BASH_REMATCH[1]}"
      value="${value//\\\"/\"}"                    # \" -> "
    elif [[ "$value" =~ ^\'(.*)\'[[:space:]]*(#.*)?$ ]]; then
      value="${BASH_REMATCH[1]}"
    else
      value="${value%%[[:space:]]#*}"              # strip " # comment"
      value="${value%"${value##*[![:space:]]}"}"   # trim trailing spaces
    fi
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    printf -v "$key" '%s' "$value"
    export "$key"
  done < "$1"
}
load_env_file "$ENV_FILE"

GCLOUD="${GCLOUD:-$(command -v gcloud || echo "$HOME/google-cloud-sdk/bin/gcloud")}"
TEMPORAL_CLI="${TEMPORAL_CLI:-temporal}"
DRY_RUN="${DRY_RUN:-0}"

# Names used across the scripts.
AR_REPO="petkit"
API_SERVICE="petkit-api"
API_SA_NAME="petkit-api"
WORKER_SA_NAME="petkit-worker"
WORKER_DEPLOYMENT_NAME="petkit"
WORKER_POOL_PREFIX="petkit-worker"

require() {
  local missing=()
  for name in "$@"; do
    [[ -n "${!name:-}" ]] || missing+=("$name")
  done
  if ((${#missing[@]})); then
    echo "Set these in $ENV_FILE: ${missing[*]}" >&2
    exit 1
  fi
}

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

# Run a command that changes something (or just print it in dry-run mode).
run() {
  if [[ "$DRY_RUN" == "1" ]]; then
    # stderr, so callers' `>/dev/null` (hiding real output) doesn't hide this.
    { printf '+ %q' "$1"; shift; printf ' %q' "$@"; printf '\n'; } >&2
  else
    "$@"
  fi
}

sa_email() { echo "$1@${GOOGLE_CLOUD_PROJECT}.iam.gserviceaccount.com"; }
