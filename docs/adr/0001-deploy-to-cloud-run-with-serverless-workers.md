# 0001. Deploy to Cloud Run with Temporal Serverless Workers

- Status: Accepted (2026-11-06; details in [0008](0008-one-image-api-service-and-serverless-worker-pool.md))
- Date: 2026-11-04

> **Update (2026-11-06):** Serverless Workers for Cloud Run are now in public
> preview with no access request, Python is supported (SDK 1.35+), and Temporal
> Cloud on GCP runs in `us-west1`, so everything deploys there.

## Context

The app runs on a laptop: a FastAPI server with an embedded Temporal worker, a
local `temporal server start-dev`, and a React frontend. Feeds are missed or
delayed whenever the laptop sleeps (observed 2026-11-05: a 13:00 scheduled feed
fired at 13:03 and took 6 minutes to verify because the Mac was only waking
briefly).

We want to host it on GCP, and to use it as a demo of Temporal Serverless
Workers on Cloud Run, where Temporal Cloud scales a Cloud Run worker pool in our
project based on task-queue load, down to zero when idle.

Constraints:

- **Cloud Run throttles CPU and scales to zero between requests.** A worker that
  long-polls inside a request-based service stalls unless it's pinned always-on.
- **PetKit allows one login session per account.** Two processes with their own
  `PetKitClient` log each other out.
- **Serverless Workers require Temporal Cloud.** The Temporal service can't run
  in Cloud Run (no durable disk). As of 2026-11 the Cloud Run integration is in
  public preview, needs access via Temporal support, and requires Worker
  Versioning. Python SDK support for the Cloud Run integration still needs
  confirming.
- **Scale-in can kill an instance mid-activity.** In-memory state on the worker
  is unreliable.

## Decision

Target architecture:

- **Temporal Cloud** hosts the Temporal service.
- **Worker:** a Cloud Run worker pool managed by Temporal Serverless Workers,
  capped at one instance, and the only process that talks to PetKit
  (see [0006](0006-worker-is-the-only-petkit-client.md)).
- **API:** a request-based Cloud Run service that scales to zero. It verifies
  Firebase ID tokens ([0005](0005-firebase-auth-with-email-allowlist.md)) and
  talks only to Temporal.
- **Reads:** the worker writes device snapshots to Firestore, and the frontend
  reads them live. Dashboard loads never wake the worker.
- **Secrets** (PetKit credentials, Temporal API key) live in Secret Manager.

Implementation order:

1. Schedules + short-lived `FeedWorkflow` ([0002](0002-temporal-schedules-and-short-lived-feed-workflow.md)). Done.
2. Move PetKit writes behind Temporal.
3. Firestore snapshots, move reads, remove PetKit from the API.
4. Firebase Auth.
5. Containerize and deploy the worker pool and API service.

## Alternatives considered

- **One Cloud Run service, min = max = 1 instance, CPU always allocated**, with
  the worker still embedded. Simplest, and it keeps the single PetKit session.
  It doesn't show off serverless workers and never scales to zero. This remains
  the fallback if the preview isn't usable for Python.
- **A small Compute Engine VM running everything**, including self-hosted
  Temporal. Cheapest per month, but we'd maintain the VM and Temporal, and it
  isn't a serverless story.
- **Serverless workers bolted onto the original architecture.** Rejected: the
  long-running per-feeder workflow made Worker Versioning painful, the API and
  worker would fight over the PetKit session, and the in-memory dedup cache
  wouldn't survive scale-in.

## Consequences

- Nothing runs between feeds except Temporal Cloud and Firestore, both managed.
  That's the demo story.
- Manual feeds and refreshes pay a worker cold start of a few seconds. The UI
  must show "queued" and let the Firestore update complete it.
- Each worker cold start logs in to PetKit again. That's no worse than today; the
  app already logs out the PetKit mobile app.
- Depends on a preview feature. Status stays Proposed until we've confirmed
  Python support and access.
- Temporal Cloud has a monthly cost, which is notable for a single-feeder app.
