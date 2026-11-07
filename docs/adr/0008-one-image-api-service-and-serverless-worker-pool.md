# 0008. One image: API as a Cloud Run service, worker as a Serverless Worker pool

- Status: Accepted (deployment not yet run)
- Date: 2026-11-06

## Context

[0001](0001-deploy-to-cloud-run-with-serverless-workers.md) set the target:
Cloud Run plus Temporal Serverless Workers. Facts confirmed on 2026-11-06:

- Serverless Workers for Cloud Run are in **public preview**, with no access
  request needed. Python is supported, through `temporalio.contrib.gcp.cloud_run`,
  which first appears in SDK 1.35 (we were on 1.22).
- Temporal Cloud on GCP runs in **`us-west1`**. Firestore moved there too
  ([0007](0007-browser-reads-through-the-api.md)).
- Serverless Workers require **Worker Versioning**: one Cloud Run worker pool
  per build ID, registered as a Worker Deployment Version that Temporal Cloud
  scales by impersonating an "invoker" service account.
- Cloud Run gives a worker 10 s between SIGTERM and SIGKILL, and Temporal's
  scale-in can stop an instance mid-activity.

## Decision

- **One container image, two roles**, chosen by `PETKIT_ROLE`:
  - `api` (default): uvicorn serving `/api` and the built frontend from the same
    origin. Deployed as Cloud Run service `petkit-api`, scaling 0–2, private
    (`--no-allow-unauthenticated`; open it with `gcloud run services proxy`).
    Auth for other users is a later step ([0005](0005-firebase-auth-with-email-allowlist.md)).
  - `worker`: `python -m backend.temporal.worker`. Deployed as worker pool
    `petkit-worker-<build-id>`, min 0 / max **1** instance (PetKit allows one
    session).
  - An env var instead of a `--command` override because it works the same for
    services, worker pools, and `docker run`.
- **Build ID = git commit** (short SHA). The deploy script refuses a dirty
  tree unless `ALLOW_DIRTY=1`, which adds a suffix.
- **Every workflow is `PINNED`** (worker default). All our workflows finish
  within about an hour ([0002](0002-temporal-schedules-and-short-lived-feed-workflow.md)),
  so a run always completes on the code it started with, and an old pool
  drains within minutes of a deploy. No patching or replay-compatibility work
  needed between versions.
- **Versioning is off locally**: it turns on only when both
  `WORKER_DEPLOYMENT_NAME` and `WORKER_BUILD_ID` are set.
- **Connection settings use Temporal's standard names** (`TEMPORAL_ADDRESS`,
  `TEMPORAL_NAMESPACE`, `TEMPORAL_API_KEY`; `TEMPORAL_HOST` still works). An API
  key turns on TLS. On Cloud Run we use the regional endpoint
  `us-west1.gcp.api.temporal.io:7233`.
- **The API ensures the refresh schedule at startup**, not the worker, because
  on Cloud Run the API has no worker. In-process workers are still the local
  default (`RUN_EMBEDDED_WORKER`).
- **The worker shuts down gracefully on SIGTERM**, with 8 s for in-flight
  activities. A killed `trigger_feed` is already safe: it's never retried
  blindly, and the workflow checks the feed log before deciding anything
  ([0003](0003-at-most-once-feed-with-polled-verification.md)).
- **Secrets** (PetKit credentials, Temporal API key) live in Secret Manager.
  The API's service account can't read the PetKit credentials.
- Setup and deploy are shell scripts (`scripts/cloud-setup.sh`,
  `scripts/deploy.sh`) with a `DRY_RUN=1` mode. The one exception is the
  invoker service account, which comes from Temporal's Terraform module.

## Alternatives considered

- **Separate images for API and worker.** Twice the builds for no real
  isolation benefit; the code is shared.
- **`AUTO_UPGRADE` versioning.** Lets running workflows move to new code, but
  then every change has to be replay-safe. Pointless when runs last minutes.
- **Terraform for everything.** More reproducible, but heavier for a demo
  repo; scripts are easier to show on video. Only the invoker uses Terraform,
  because that's how Temporal ships it.
- **An always-on worker as a stopgap.** Planned while we thought preview access
  needed a support ticket. Not needed.

## Consequences

- Each deploy leaves the previous worker pool behind until someone deletes it
  (docs/deploy.md has the commands).
- The local backend and the cloud worker must not run at the same time: they'd
  share one PetKit account, and the laptop's local schedule would feed in
  parallel with the cloud one.
- The image build itself hasn't been run yet (no Docker locally); its pieces
  were checked separately: frozen dependency install, frontend build, the API in
  container mode, and the versioned worker against a dev server.
