# 0005. Firebase Auth with an email allowlist

- Status: Superseded by [0010](0010-identity-aware-proxy-for-access.md) (IAP)
- Date: 2026-11-04

> **Note (2026-11-06):** [0007](0007-browser-reads-through-the-api.md) keeps the
> browser away from Firestore, which removes the main reason below for choosing
> Firebase Auth over IAP. Reconsider IAP before implementing.

## Context

On a laptop the app needs no authentication. Deployed to a public Cloud Run URL
([0001](0001-deploy-to-cloud-run-with-serverless-workers.md)), anyone who finds
it could dispense food or change feeder settings. There is one intended user,
maybe a few later.

Firestore will hold device snapshots that the frontend reads directly, so
whatever identity we use must also work in Firestore security rules.

## Decision

- **Frontend:** Firebase Auth with Google sign-in. The axios client attaches the
  Firebase ID token as `Authorization: Bearer …`.
- **API:** a FastAPI `require_user` dependency applied to the whole feeders
  router. It verifies the token with `firebase-admin` and returns 403 unless the
  email is verified and listed in `ALLOWED_EMAILS`. `GET /api/health` stays
  public.
- **Firestore rules** check the same allowlist, so the browser can read
  snapshots directly.
- The Cloud Run service allows unauthenticated invocation at the IAM level; the
  app enforces access.

## Alternatives considered

- **Identity-Aware Proxy (IAP) directly on Cloud Run.** No auth code in the app:
  allowlist a Google account in IAM. Less to get wrong for one user. Rejected
  because IAP doesn't help with Firestore reads from the browser, which the
  target architecture relies on. Revisit if we drop Firestore.
- **No allowlist, any signed-in Google account.** Unacceptable: anyone could
  feed the pet.

## Consequences

- The allowlist is the real security boundary. Without it, Firebase sign-in
  alone admits every Google account.
- The deployed URL must be added to Firebase's authorized domains.
- Router tests override `require_user` with `app.dependency_overrides`; new
  tests cover 401 (missing or bad token) and 403 (email not allowed).
- Firebase config values in the frontend are public by design; nothing secret
  ships to the browser.
