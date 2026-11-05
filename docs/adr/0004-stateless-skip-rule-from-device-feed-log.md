# 0004. Stateless skip rule from the device's feed log

- Status: Accepted
- Date: 2026-11-05
- Implemented in: `816c6fa`

## Context

The original rule: after a successful manual feed, skip the next scheduled feed
no matter how far away it is. It was implemented as a `skip_next` flag in the
long-running workflow's memory. Once each feed became its own short-lived
workflow ([0002](0002-temporal-schedules-and-short-lived-feed-workflow.md)),
there was nowhere to keep that flag.

## Decision

- A scheduled `FeedWorkflow` first runs `check_recent_feed`. If the device's feed
  log shows any feed within `FEED_SKIP_WINDOW_HOURS` (default 12), the run
  returns `skipped` without sending a command.
- Manual feeds never skip.
- The window is read from config **by the activity on each run**, not stored in
  the schedule. Changing it takes a worker restart; schedules don't need to be
  re-saved. `0` disables skipping.

## Alternatives considered

- **Store the flag in the schedule memo or a database.** Keeps the exact old
  semantics, but brings state back, which is what 0002 removed.
- **Count only feeds made through this app**, by listing recent manual
  `FeedWorkflow`s through visibility. More precise, but ignores feeds from the
  PetKit app or the button on the device, which should also prevent a double feed.
- **Store the window in the schedule's workflow input.** That was the first
  implementation. Rejected after testing showed the trap: changing `.env` had no
  effect until someone re-saved the schedule.

## Consequences

- Semantics changed: "skip if fed in the last N hours, by any means," instead of
  "skip the next one after a manual feed in this app." A manual feed 20 hours
  before a scheduled one no longer skips it (with the default 12).
- The window must stay under 24 hours, or each daily feed will skip the next.
- Verified on real hardware, 2026-11-05: a triggered scheduled run skipped, and
  the reported last-feed time matched the manual feed to the second, which also
  confirmed timezone handling of the D4 feed log.
- **Known gap: manual and scheduled feeds can overlap.** If "Feed now" is pressed
  seconds before the schedule fires, the device log may not show it yet and the
  pet could be fed twice. The old single workflow made them take turns. Possible
  fix: have the scheduled run check for a running `feed-{id}-manual` workflow.
- **Gotcha worth knowing:** the Temporal Python data converter silently ignores
  unknown dataclass fields. Removing `skip_if_fed_within_hours` from `FeedInput`
  was safe for existing schedules for that reason. But renaming a field, for
  example to a boolean flag, would have silently read as its default for every
  saved schedule. `tests/test_schedules.py` covers decoding the old input.
