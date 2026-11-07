# Architecture Decision Records

Short records of significant design decisions: what we chose, why, and what we
gave up. Format follows [Michael Nygard's ADR template](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions).

## Conventions

- One decision per file: `NNNN-short-title.md`, numbered in order.
- Records are never deleted. When a decision changes, write a new ADR and set
  the old one's status to `Superseded by NNNN`.
- Status is one of: `Proposed`, `Accepted`, `Superseded by NNNN`, `Rejected`.
- Keep the **Consequences** section honest, including known gaps. Most of the
  value is in recording what we knowingly traded away.

## Index

| ADR | Title | Status |
|-----|-------|--------|
| [0001](0001-deploy-to-cloud-run-with-serverless-workers.md) | Deploy to Cloud Run with Temporal Serverless Workers | Accepted |
| [0002](0002-temporal-schedules-and-short-lived-feed-workflow.md) | Temporal Schedules + short-lived FeedWorkflow | Accepted |
| [0003](0003-at-most-once-feed-with-polled-verification.md) | Send feeds at most once; verify by polling through activity retries | Accepted |
| [0004](0004-stateless-skip-rule-from-device-feed-log.md) | Stateless skip rule from the device's feed log | Accepted |
| [0005](0005-firebase-auth-with-email-allowlist.md) | Firebase Auth with an email allowlist | Proposed (revisit) |
| [0006](0006-worker-is-the-only-petkit-client.md) | The worker is the only PetKit client; move writes first, reads with Firestore | Accepted |
| [0007](0007-browser-reads-through-the-api.md) | Browser reads snapshots through the API; Firestore stays locked down | Accepted |
| [0008](0008-one-image-api-service-and-serverless-worker-pool.md) | One image: API as a Cloud Run service, worker as a Serverless Worker pool | Accepted |

## Template

```markdown
# NNNN. Title

- Status: Proposed | Accepted | Superseded by NNNN
- Date: YYYY-MM-DD

## Context
What forces the decision: constraints, the problem, what we knew at the time.

## Decision
What we chose.

## Alternatives considered
Options we rejected, and why.

## Consequences
What gets easier, what gets harder, known gaps.
```
