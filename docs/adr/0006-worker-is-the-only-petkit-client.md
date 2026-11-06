# 0006. The worker is the only PetKit client; move writes first, reads with Firestore

- Status: Accepted
- Date: 2026-11-05

## Context

There's no direct connection to the feeder: every action goes through PetKit's
cloud API, which allows **one login session per account**.

After [0002](0002-temporal-schedules-and-short-lived-feed-workflow.md), feeds go
through Temporal, but the FastAPI routers still call PetKit directly for
everything else:

- **Writes:** update settings, reset desiccant, food replenished
- **Reads:** feeder list and status, feed history, refresh, and the "does this
  feeder exist" check in most endpoints

Today that works because the API and the worker share one process and one
`PetKitClient`. In the target architecture
([0001](0001-deploy-to-cloud-run-with-serverless-workers.md)) they're separate
Cloud Run services, and two clients would keep logging each other out.

## Decision

- **Only Temporal activities call PetKit.** `backend/client.py` is imported only
  by activities. The API talks only to Temporal (and later Firestore).
- **Order of work:**
  - **Step 2: writes.** All three writes go through one `DeviceCommandWorkflow`
    that runs a `send_device_command` activity. These commands are idempotent,
    so unlike feeds they retry normally (3 attempts). **The API waits for the
    workflow result** (up to 25 s), because the UI refreshes the
    feeder right after a write and would otherwise show stale settings and
    report success before anything happened. Outcomes: 200 when done, 502 with
    PetKit's message on failure, 202 `pending` if the wait runs out (the
    workflow keeps running).
  - **Step 3: reads.** The `refresh_feeder_snapshots` activity writes one
    Firestore document per feeder (state, settings, feed history). It runs
    after every feed and device command, from the Refresh button, and from the
    `feeder-snapshot-refresh` Schedule every 30 minutes. The API reads those
    documents; how the browser gets them is [0007](0007-browser-reads-through-the-api.md).
    A test fails if anything under `backend/routers/` imports PetKit code.

## Alternatives considered

- **Move reads in step 2 too, before Firestore exists**, by running a workflow
  per page load and waiting for its result. Slow, wakes the worker on every
  dashboard load, and step 3 would throw it away.
- **Share one PetKit session token between API and worker** through Firestore or
  Secret Manager. Depends on `pypetkitapi` letting us inject a session (not
  confirmed), and keeps two PetKit callers to reason about.
- **Temporal Standalone Activities for writes.** Would skip the wrapper
  workflow, but Python SDK support wasn't confirmed at the time of writing.
  Revisit later.

## Consequences

- One PetKit session, held by one worker instance (pool capped at one).
- Every PetKit call gets Temporal retries and shows up in the Temporal UI, not
  just feeds.
- Writes are synchronous: the API waits up to 25 s per request (see
  [0007](0007-browser-reads-through-the-api.md) for why this stayed). On Cloud
  Run that holds a request open through a worker cold start, which is
  acceptable at this scale.
