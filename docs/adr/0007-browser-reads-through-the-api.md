# 0007. Browser reads snapshots through the API; Firestore stays locked down

- Status: Accepted
- Date: 2026-11-06

## Context

After [0006](0006-worker-is-the-only-petkit-client.md), device state lives in
Firestore snapshots written by the worker. The browser can get at them two ways:

- **Through the API.** The API reads Firestore with the server library; the
  frontend keeps polling the API.
- **Directly.** The frontend uses the Firebase SDK with live listeners,
  guarded by Firestore security rules.

The app is the subject of a YouTube video about Temporal Serverless Workers on
Cloud Run. Live updates would look nice, but the video will be edited, so
they're not essential to the story.

## Decision

- The browser reads only through the API. The API reads Firestore with the
  server library, authenticated by IAM (Application Default Credentials
  locally, the service account on Cloud Run).
- Firestore security rules deny all client access. They're version-controlled
  in `firestore.rules`, and a test fails if they ever allow anything.
- Development uses the real `petkit-temporal` project, not the emulator. The
  emulator would have needed Java and the Firebase CLI; the real project needs
  only `gcloud`, which deployment needs anyway.
- The snapshots live in a named Firestore Native database, `petkit`, in
  `us-west1`. Temporal Cloud on GCP is only available in `us-west1`, so the
  Cloud Run worker and API go there too, and Firestore follows to keep
  everything in one region. (We first created the `(default)` database in
  `us-central1`, before knowing Temporal's region. A database's location
  can't change, so we added a named one; snapshots are a cache, so nothing
  needed migrating.)
- Writes stay synchronous: the API waits for the workflow (up to 25 s), and the
  workflow refreshes the feeder's snapshot before finishing. The UI's next
  poll then sees the change. This replaces 0006's plan to make writes
  asynchronous in step 3, which only made sense with live updates.

## Alternatives considered

- **Direct browser reads with live listeners.** Needs Firebase Auth and rules
  first, pulling auth ahead of this step. Can be added later without changing
  the worker side.
- **Firestore emulator for development.** Free and resettable, but extra
  tooling. Revisit if tests ever need a real Firestore.

## Consequences

- One security boundary to get right (the API). Firestore rules only need to
  be deny-all, which is simple to verify.
- No live updates; the UI polls. Polling a scale-to-zero API costs almost
  nothing.
- **Weakens [0005](0005-firebase-auth-with-email-allowlist.md)'s main reason for
  choosing Firebase Auth over IAP** (browser reads from Firestore). Revisit 0005
  before step 4.
- Only one database per project gets Firestore's free tier, and that's
  `(default)`. The named `petkit` database is billed from the first operation;
  at this app's volume that's cents per month.
- Dashboard data can be up to `SNAPSHOT_REFRESH_MINUTES` (30) old, except right
  after an action or a manual refresh.
- A brand-new database is empty until the first refresh. The refresh schedule
  is created at startup and runs once immediately, and the UI offers
  "Load feeders from PetKit."
