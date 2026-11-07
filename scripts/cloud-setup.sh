#!/usr/bin/env bash
# One-time Google Cloud setup for the PetKit app. Safe to re-run.
#
#   scripts/cloud-setup.sh                   # uses .env.cloud
#   DRY_RUN=1 scripts/cloud-setup.sh         # show what would change
#   ROTATE_SECRETS=1 scripts/cloud-setup.sh  # push new secret values from .env.cloud
#
# Creates: APIs, an Artifact Registry repo, Secret Manager secrets, and two
# least-privilege service accounts (API service, worker pool runner).
# Does NOT create the Temporal invoker service account; that's Temporal's
# Terraform module (docs/deploy.md, step 3).

cd "$(dirname "$0")/.."
source scripts/_common.sh

require GOOGLE_CLOUD_PROJECT REGION TEMPORAL_API_KEY PETKIT_USERNAME PETKIT_PASSWORD
P="$GOOGLE_CLOUD_PROJECT"
ROTATE_SECRETS="${ROTATE_SECRETS:-0}"

step "Enabling APIs"
run "$GCLOUD" services enable \
  run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com \
  secretmanager.googleapis.com firestore.googleapis.com iam.googleapis.com \
  --project "$P"

step "Artifact Registry repository '$AR_REPO' ($REGION)"
if "$GCLOUD" artifacts repositories describe "$AR_REPO" --location "$REGION" --project "$P" >/dev/null 2>&1; then
  echo "exists"
else
  run "$GCLOUD" artifacts repositories create "$AR_REPO" \
    --repository-format docker --location "$REGION" --project "$P" \
    --description "PetKit app images"
fi

# ensure_secret NAME ENV_VAR_NAME: create the secret from the env var's value,
# or add a new version when ROTATE_SECRETS=1. Never prints the value.
ensure_secret() {
  local name="$1" var="$2"
  if "$GCLOUD" secrets describe "$name" --project "$P" >/dev/null 2>&1; then
    if [[ "$ROTATE_SECRETS" == "1" ]]; then
      if [[ "$DRY_RUN" == "1" ]]; then
        echo "+ gcloud secrets versions add $name --data-file=-  (value of $var)"
      else
        printf '%s' "${!var}" | "$GCLOUD" secrets versions add "$name" --data-file=- --project "$P" >/dev/null
        echo "$name: new version added"
      fi
    else
      echo "$name: exists (ROTATE_SECRETS=1 to update)"
    fi
  else
    if [[ "$DRY_RUN" == "1" ]]; then
      echo "+ gcloud secrets create $name --data-file=-  (value of $var)"
    else
      printf '%s' "${!var}" | "$GCLOUD" secrets create "$name" \
        --data-file=- --replication-policy automatic --project "$P" >/dev/null
      echo "$name: created"
    fi
  fi
}

step "Secrets"
ensure_secret temporal-api-key TEMPORAL_API_KEY
ensure_secret petkit-username PETKIT_USERNAME
ensure_secret petkit-password PETKIT_PASSWORD

ensure_sa() {
  local name="$1" display="$2"
  if "$GCLOUD" iam service-accounts describe "$(sa_email "$name")" --project "$P" >/dev/null 2>&1; then
    echo "$name: exists"
  else
    run "$GCLOUD" iam service-accounts create "$name" --display-name "$display" --project "$P"
    NEW_SA=1
  fi
}

step "Service accounts"
NEW_SA=0
ensure_sa "$API_SA_NAME" "PetKit API (Cloud Run service)"
ensure_sa "$WORKER_SA_NAME" "PetKit worker (Cloud Run worker pool runner)"
if [[ "$NEW_SA" == "1" && "$DRY_RUN" != "1" ]]; then
  echo "Waiting for new service accounts to propagate..."
  sleep 15
fi

grant_project_role() {
  run "$GCLOUD" projects add-iam-policy-binding "$P" \
    --member "serviceAccount:$(sa_email "$1")" --role "$2" --condition None --quiet >/dev/null
  echo "$1: $2"
}

grant_secret() {
  run "$GCLOUD" secrets add-iam-policy-binding "$2" \
    --member "serviceAccount:$(sa_email "$1")" --role roles/secretmanager.secretAccessor \
    --project "$P" --quiet >/dev/null
  echo "$1: read secret $2"
}

step "Permissions (least privilege)"
# API: reads Firestore snapshots, talks to Temporal. No PetKit credentials (ADR 0006).
grant_project_role "$API_SA_NAME" roles/datastore.viewer
grant_secret "$API_SA_NAME" temporal-api-key
# Worker: writes Firestore snapshots, talks to Temporal and PetKit.
grant_project_role "$WORKER_SA_NAME" roles/datastore.user
grant_secret "$WORKER_SA_NAME" temporal-api-key
grant_secret "$WORKER_SA_NAME" petkit-username
grant_secret "$WORKER_SA_NAME" petkit-password

step "Done"
echo "Next: create the Temporal invoker service account (docs/deploy.md, step 3),"
echo "put its email in $ENV_FILE as TEMPORAL_INVOKER_SA, then run scripts/deploy.sh."
