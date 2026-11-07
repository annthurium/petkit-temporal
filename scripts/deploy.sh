#!/usr/bin/env bash
# Build and deploy the PetKit app to Cloud Run with a Temporal Serverless Worker.
#
#   scripts/deploy.sh                 # uses .env.cloud; deploys the current commit
#   DRY_RUN=1 scripts/deploy.sh       # show what would happen
#   ALLOW_DIRTY=1 scripts/deploy.sh   # deploy uncommitted changes (build ID gets a suffix)
#
# Each deploy:
#   1. builds one image (Cloud Build), tagged with the build ID (git commit)
#   2. deploys the API as a Cloud Run service (private: IAM-authenticated only)
#   3. creates a NEW worker pool for this build ID, starting at 0 instances
#   4. registers the build as a Temporal Worker Deployment Version that
#      Temporal Cloud scales (max 1 instance: PetKit allows one session)
#   5. makes it the current version, so new workflows run on it
# Old worker pools keep serving pinned workflows until they finish (minutes;
# ADR 0002/0008); delete them afterwards (docs/deploy.md).

cd "$(dirname "$0")/.."
source scripts/_common.sh

require GOOGLE_CLOUD_PROJECT REGION FIRESTORE_DATABASE TEMPORAL_ADDRESS \
  TEMPORAL_NAMESPACE TEMPORAL_API_KEY TEMPORAL_INVOKER_SA PETKIT_TIMEZONE
P="$GOOGLE_CLOUD_PROJECT"
TEMPORAL_TASK_QUEUE="${TEMPORAL_TASK_QUEUE:-petkit-tasks}"
PETKIT_REGION="${PETKIT_REGION:-US}"
FEED_SKIP_WINDOW_HOURS="${FEED_SKIP_WINDOW_HOURS:-12}"

# --- Build ID: the commit being deployed ------------------------------------
BUILD_ID="$(git rev-parse --short=7 HEAD)"
if [[ -n "$(git status --porcelain)" ]]; then
  if [[ "${ALLOW_DIRTY:-0}" != "1" ]]; then
    echo "Uncommitted changes. Commit first, or set ALLOW_DIRTY=1." >&2
    exit 1
  fi
  BUILD_ID="${BUILD_ID}-dirty-$(date +%Y%m%d%H%M%S)"
fi
IMAGE="${REGION}-docker.pkg.dev/${P}/${AR_REPO}/petkit:${BUILD_ID}"
WORKER_POOL="${WORKER_POOL_PREFIX}-${BUILD_ID}"
echo "Build ID: $BUILD_ID"
echo "Image:    $IMAGE"

COMMON_ENV="GOOGLE_CLOUD_PROJECT=${P},FIRESTORE_DATABASE=${FIRESTORE_DATABASE}"
COMMON_ENV+=",TEMPORAL_ADDRESS=${TEMPORAL_ADDRESS},TEMPORAL_NAMESPACE=${TEMPORAL_NAMESPACE}"
COMMON_ENV+=",TEMPORAL_TASK_QUEUE=${TEMPORAL_TASK_QUEUE},PETKIT_TIMEZONE=${PETKIT_TIMEZONE}"
COMMON_ENV+=",FEED_SKIP_WINDOW_HOURS=${FEED_SKIP_WINDOW_HOURS}"

step "1/5 Build image with Cloud Build"
run "$GCLOUD" builds submit --tag "$IMAGE" --project "$P" --region "$REGION" .

step "2/5 Deploy API service '$API_SERVICE'"
run "$GCLOUD" run deploy "$API_SERVICE" \
  --image "$IMAGE" --region "$REGION" --project "$P" \
  --service-account "$(sa_email "$API_SA_NAME")" \
  --no-allow-unauthenticated \
  --min-instances 0 --max-instances 2 \
  --set-env-vars "${COMMON_ENV},PETKIT_ROLE=api,RUN_EMBEDDED_WORKER=false" \
  --set-secrets "TEMPORAL_API_KEY=temporal-api-key:latest"

step "3/5 Create worker pool '$WORKER_POOL' (0 instances; Temporal scales it)"
run "$GCLOUD" run worker-pools deploy "$WORKER_POOL" \
  --image "$IMAGE" --region "$REGION" --project "$P" \
  --service-account "$(sa_email "$WORKER_SA_NAME")" \
  --instances 0 \
  --set-env-vars "${COMMON_ENV},PETKIT_ROLE=worker,PETKIT_REGION=${PETKIT_REGION},WORKER_DEPLOYMENT_NAME=${WORKER_DEPLOYMENT_NAME},WORKER_BUILD_ID=${BUILD_ID},TEMPORAL_CLOUD_RUN_IDENTITY=true" \
  --set-secrets "TEMPORAL_API_KEY=temporal-api-key:latest,PETKIT_USERNAME=petkit-username:latest,PETKIT_PASSWORD=petkit-password:latest"

# The Temporal CLI reads TEMPORAL_ADDRESS / TEMPORAL_NAMESPACE / TEMPORAL_API_KEY
# from the environment (exported from .env.cloud above).
step "4/5 Register Temporal Worker Deployment Version $WORKER_DEPLOYMENT_NAME/$BUILD_ID"
if "$TEMPORAL_CLI" worker deployment describe --name "$WORKER_DEPLOYMENT_NAME" >/dev/null 2>&1; then
  echo "Worker deployment '$WORKER_DEPLOYMENT_NAME' exists"
else
  run "$TEMPORAL_CLI" worker deployment create --name "$WORKER_DEPLOYMENT_NAME"
fi
# max-instances 1: PetKit allows a single login session (ADR 0001, 0006).
run "$TEMPORAL_CLI" worker deployment create-version \
  --deployment-name "$WORKER_DEPLOYMENT_NAME" --build-id "$BUILD_ID" \
  --gcp-cloud-run-project "$P" --gcp-cloud-run-region "$REGION" \
  --gcp-cloud-run-worker-pool "$WORKER_POOL" \
  --gcp-cloud-run-service-account "$TEMPORAL_INVOKER_SA" \
  --gcp-cloud-run-min-instances 0 --gcp-cloud-run-max-instances 1 \
  --gcp-cloud-run-initial-instances 0 --gcp-cloud-run-utilization-target 0.8 \
  --gcp-cloud-run-scale-down-stabilization-duration 90s

step "5/5 Make $BUILD_ID the current version"
run "$TEMPORAL_CLI" worker deployment set-current-version \
  --deployment-name "$WORKER_DEPLOYMENT_NAME" --build-id "$BUILD_ID" --yes

step "Deployed $BUILD_ID"
cat <<EOF
Open the app (authenticates with your gcloud credentials):
  $GCLOUD run services proxy $API_SERVICE --region $REGION --project $P --port 8080
  then browse to http://localhost:8080

Watch the worker pool scale (0 when idle):
  $GCLOUD run worker-pools describe $WORKER_POOL --region $REGION --project $P
  $GCLOUD run worker-pools logs read $WORKER_POOL --region $REGION --project $P
EOF
