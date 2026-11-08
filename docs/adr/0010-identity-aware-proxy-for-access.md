# 0010. Identity-Aware Proxy on Cloud Run for access control

- Status: Accepted
- Date: 2026-11-08
- Supersedes: [0005](0005-firebase-auth-with-email-allowlist.md)

## Context

The API service was deployed private (`--no-allow-unauthenticated`), reachable
only through `gcloud run services proxy` on the laptop. That's awkward day to
day, impossible from a phone, and shows a `localhost` URL on video. Making the
service public without auth is not an option: anyone could feed the pet.

[0005](0005-firebase-auth-with-email-allowlist.md) proposed Firebase Auth,
mainly so the browser could read Firestore directly. [0007](0007-browser-reads-through-the-api.md)
decided the browser never reads Firestore, which removed that reason.

Facts checked on 2026-11-08:

- IAP integrates directly with Cloud Run (no load balancer): `--iap` on the
  service, `roles/run.invoker` for the IAP service agent, and
  `roles/iap.httpsResourceAccessor` for allowed users.
- The project belongs to a Google Workspace organization, and the user signs in
  with an account in it, so IAP's **Google-managed OAuth client** works. No
  custom OAuth client or consent screen is needed. Users outside the org would
  need a custom OAuth client with an External brand, which can only be created
  in the console.

## Decision

- Enable IAP on `petkit-api`, keeping `--no-allow-unauthenticated`. Cloud Run
  then admits only principals with `run.invoker`: the IAP service agent, plus
  project owners calling it directly.
- Access is an IAM allowlist: `IAP_ALLOWED_MEMBERS` in `.env.cloud`
  (comma-separated `user:` / `group:` members), granted
  `roles/iap.httpsResourceAccessor` on the service by `scripts/deploy.sh`.
  Default: the deploying gcloud account.
- `cloud-setup.sh` enables the IAP API and creates the IAP service agent through
  the Service Usage REST API (the gcloud command for that is beta-only).
  `deploy.sh` always passes `--iap`, so a redeploy can't turn it off.
- The app has no auth code. The frontend sends `X-Requested-With:
  XMLHttpRequest`, so an expired IAP session yields 401 instead of a redirect
  scripts can't follow, and it reloads the page on 401 (at most once a minute)
  to sign in again.

## Alternatives considered

- **Firebase Auth with an email allowlist** ([0005](0005-firebase-auth-with-email-allowlist.md)).
  Real login UI and works for any Google account, but needs token verification
  in the API, an auth flow in the frontend, and tests for both. No longer
  justified once the browser stopped reading Firestore.
- **Keep the gcloud proxy.** Zero setup, but laptop-only.
- **IAP behind an external HTTPS load balancer.** Needed for custom domains or
  Cloud Armor; extra cost and moving parts we don't need. (IAP can't be
  configured on both the load balancer and the service.)

## Consequences

- The real `run.app` URL works from any browser, including a phone, after
  Google sign-in.
- Adding a user means one IAM binding (or a Google Group in
  `IAP_ALLOWED_MEMBERS`). Users outside the Workspace org would require a custom
  OAuth client.
- `gcloud run services proxy` no longer gets through, because IAP is enforced
  before IAM checks.
- The API doesn't verify the IAP JWT header itself. It relies on Cloud Run's
  invoker check (only the IAP agent and project owners can call it). Verifying
  `x-goog-iap-jwt-assertion` in the app would add defense in depth if the
  invoker list ever widened.
