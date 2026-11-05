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
  - **Step 2: writes.** Each write becomes a small workflow (or one shared
    "device command" workflow) that runs an activity. The API starts it and
    returns.
  - **Step 3: reads.** Activities write device snapshots (state, settings, feed
    history, last result) to Firestore after each action and on refresh. The
    frontend reads Firestore live. Then the API's last PetKit calls, including
    the feeder-exists checks, are removed.

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
- Between steps 2 and 3, the API still reads from PetKit directly. That's fine
  locally, where the API and worker share a process, but the app isn't ready to
  split into two services until step 3 is done.
- Writes become asynchronous: the API returns once the workflow starts, and the
  UI learns the outcome later (from Firestore, after step 3).
