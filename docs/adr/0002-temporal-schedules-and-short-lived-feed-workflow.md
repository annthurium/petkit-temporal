# 0002. Temporal Schedules + short-lived FeedWorkflow

- Status: Accepted
- Date: 2026-11-05
- Implemented in: `816c6fa`

## Context

The original design ran one `DailyScheduledFeedingWorkflow` per feeder, forever:

- a timer loop computed the wait until the next feed, with manual timezone-offset
  math to work around a sandbox restriction on `ZoneInfo` (it didn't handle DST
  changes)
- manual feeds arrived as a signal to that workflow
- state lived in workflow memory: `skip_next`, `last_alert`, `last_feed_result`,
  exposed through a query
- `continue_as_new` every 100 iterations, carrying state forward

The long-running design was chosen because the "skip the next scheduled feed
after a manual feed" rule needs state that lasts between feeds, and one workflow
gave us a natural place for that state, a signal target, and a query target.

That shape conflicts with serverless workers ([0001](0001-deploy-to-cloud-run-with-serverless-workers.md)):
Worker Versioning needs old worker pools kept alive as long as pinned workflows
run, and a workflow that never ends pins forever. Also, a query needs a live
worker, so every status check would wake the pool.

## Decision

- Each feeder gets one **Temporal Schedule**, `feeding-schedule-{device_id}`, with
  a daily calendar spec and `time_zone_name = PETKIT_TIMEZONE`. Overlap policy:
  skip. Catch-up window: 30 minutes.
- Every feed, scheduled or manual, is a short-lived **`FeedWorkflow`** with a
  1-hour execution timeout. Workflow IDs: `feed-{id}-scheduled-<timestamp>` (from
  the schedule) and `feed-{id}-manual`.
- A manual feed starts its own `FeedWorkflow` with a fixed ID and
  `WorkflowIDConflictPolicy.FAIL`: one manual feed at a time per device, so a
  double-click returns 409 instead of feeding twice.
- **No state carried between runs.** The skip rule became stateless
  ([0004](0004-stateless-skip-rule-from-device-feed-log.md)). "Latest result"
  is read from the most recent closed `FeedWorkflow` through the visibility API.
- Schedule CRUD and status reads only talk to the Temporal server; no worker
  needed.
- `backend/temporal/migrate_to_schedules.py` converts any still-running legacy
  workflows using their memo.

## Alternatives considered

- **Keep the long-running workflow.** Rejected for the versioning and wake-up
  reasons above.
- **Store `skip_next` in the schedule's memo, or have the manual feed workflow
  update the schedule.** Keeps the old semantics, but moves state into an odd
  place and couples the two workflows.
- **Cloud Scheduler instead of Temporal Schedules.** Would split orchestration
  across two systems and lose the Temporal UI view of schedules and runs.

## Consequences

- Removed: the timezone workaround and its DST bug, `continue_as_new`, signals,
  queries, the 409 when no schedule is running. The workflow went from ~300
  lines with a loop to a single pass.
- The Temporal UI shows schedules with next run times, and Pause, Trigger, and
  Backfill come built in.
- Saving a schedule replaces it; each feeder has exactly one.
- **No late feeds beyond 1 hour.** If no worker picks up a run within the
  execution timeout, it times out and shows as a failure. The old workflow would
  have fed whenever the worker returned.
- **Visibility lag.** The latest result comes from the visibility index, which
  can lag by up to about a second after a run closes.
- **Manual and scheduled feeds can now overlap**; see [0004](0004-stateless-skip-rule-from-device-feed-log.md).
