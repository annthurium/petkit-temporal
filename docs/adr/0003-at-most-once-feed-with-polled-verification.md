# 0003. Send feeds at most once; verify by polling through activity retries

- Status: Accepted
- Date: 2026-11-05
- Implemented in: `816c6fa`

## Context

Feeding is not idempotent: sending the PetKit feed command twice dispenses
twice. PetKit offers no webhooks or callbacks, so the only way to learn whether
the device dispensed is to read its feed log, which PetKit's cloud updates
asynchronously.

The original design:

- `trigger_feed` ran with 3 automatic retries. An in-memory cache keyed by
  activity ID suppressed repeats, but it disappears on worker restart, and with
  serverless workers scale-in can kill an instance mid-activity.
- Verification slept 12 seconds and checked the log **once**. A slow PetKit
  update made a successful feed look like a failure.
- This design was criticized as "fire and forget." Polling itself wasn't the
  problem; there's no alternative without webhooks. The weak parts were the
  single fixed-delay check and blind retries of a non-idempotent call.

## Decision

- `trigger_feed` runs with `maximum_attempts=1`. Temporal never retries it.
  The one exception happens inside the activity: if PetKit reports an expired
  session, the activity logs in again and resends. That's safe because PetKit
  rejected the first request before acting on it.
- `verify_feed` checks the feed log once per call and raises a retryable
  `FeedNotYetRecorded` error if the feed isn't there yet. The **retry policy is
  the polling loop**: 10-second interval, no backoff, about 6 checks, after a
  10-second first wait. This follows Temporal's guidance for infrequent polling.
- The workflow drives any second attempt:
  1. send the command (once)
  2. poll the feed log
  3. if still unconfirmed, send the command once more and poll again
  4. if still unconfirmed, alert
- A failed `trigger_feed` (for example a timeout) is ambiguous, so the workflow
  checks the feed log before deciding anything. Only `FeederNotFound` skips
  verification.
- The verification threshold is the time the attempt started, with a 90-second
  tolerance for PetKit's timestamp resolution.

## Alternatives considered

- **Keep retries plus a durable dedup record (Firestore or similar).** Adds a
  dependency and still has a window between "command sent" and "record written."
- **A long-running verify activity that heartbeats while polling.** Temporal's
  pattern for frequent polling. Overkill for checks every 10 seconds, and it
  holds a worker slot the whole time.
- **No re-send at all; alert on the first unconfirmed attempt.** Simpler, but
  gives up recovering from a dropped command.

## Consequences

- At most one send per attempt, at most two attempts. Missed feeds raise an
  alert; double feeds can only happen if PetKit takes longer than the whole
  polling window (~70 s) to record a feed that did dispense.
- Every check shows up in the Temporal UI as an activity attempt with its error.
- Measured on real hardware (D4 Fresh Element Solo), 2026-11-05: a manual feed
  verified on the first check, within the 10-second first wait. The polling
  window has plenty of room.
- Offline-device failures take about 2 minutes to alert (two attempts × polling
  window).
- Verification relies on the D4 feed-statistics endpoint. Other feeder models
  will need their own feed-log lookup.
