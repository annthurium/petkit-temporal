# 0009. Never re-send a feed command PetKit accepted

- Status: Accepted
- Date: 2026-11-08
- Supersedes: the re-send rule in [0003](0003-at-most-once-feed-with-polled-verification.md)

## Context

[0003](0003-at-most-once-feed-with-polled-verification.md) sends each feed
command at most once per attempt, polls the device's feed log for about 70
seconds, and, if the feed doesn't appear, sends the command a second time. It
assumed the feed log is reliable: a double feed could only happen if PetKit took
longer than the polling window to record a feed that did dispense.

That assumption failed on 2026-11-08, the first manual feed after deploying to
Cloud Run:

```
01:41:28  trigger_feed  -> ok                  (feeder dispensed)
          verify_feed   -> 6 checks over ~60 s, "Feed not yet recorded"
01:42:29  trigger_feed  -> ok                  (feeder dispensed again)
01:42:39  verify_feed   -> found the 01:42:29 feed
```

Six minutes later PetKit's feed log still showed only the second feed. The
first one was never recorded at all. Earlier feeds from the laptop were logged
within 10 seconds, so this is intermittent. A plausible but unconfirmed cause:
the worker had just cold-started and logged in to PetKit right before sending.

The feed log can miss a real feed, so it can't decide whether to dispense again.

## Decision

- **Re-send only after an explicit rejection.** If PetKit answers the feed
  command with an error (`FeedRejected`, e.g. "Device is offline"), nothing was
  dispensed. Wait 30 s and send once more.
- **Never re-send once PetKit accepted the command, or when the outcome is
  unknown** (timeout, network error, worker stopped mid-activity). Check the
  feed log; if it doesn't confirm the feed, alert: "Feed sent but the feeder
  never confirmed it. Check the feeder before feeding again."
- `trigger_feed` itself is still never retried by Temporal
  (`maximum_attempts=1`), and still re-logs-in once on an expired session,
  where PetKit rejected the request before acting on it.
- The feed log stays useful for confirmation, alerts, and history; it just no
  longer controls whether food comes out.

## Alternatives considered

- **Keep re-sending but poll longer.** The missing feed never appeared, so no
  window would have been long enough.
- **Check another signal before re-sending** (the device's `manual_feed`
  status, the food level). Unproven, and we just learned what trusting an
  unproven signal costs.
- **No retry at all, even after a rejection.** Simpler, but gives up the
  recovery from a briefly offline feeder that costs nothing when PetKit
  explicitly refused the command.

## Consequences

- A feed can now end as an *unconfirmed* failure even though food was
  dispensed. That's the intended trade: a false alarm you can check beats a
  silent double feed.
- An offline feeder alerts after about 30 seconds (two rejected sends), instead
  of the roughly 2 minutes in 0003.
- Covered by tests, including a replay of the incident against a real,
  sandboxed worker: one send, then an alert.
- Worth finding out: whether feeds sent right after a fresh PetKit login are
  the ones that go missing from the log. If so, a warm-up read before sending
  might make confirmations reliable, but it wouldn't change this rule.
