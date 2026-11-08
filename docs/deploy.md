# Deploying to Cloud Run with Temporal Serverless Workers

Architecture: [ADR 0001](adr/0001-deploy-to-cloud-run-with-serverless-workers.md) and
[ADR 0008](adr/0008-one-image-api-service-and-serverless-worker-pool.md).

```
Browser ──(gcloud run services proxy)──► petkit-api        Cloud Run service, scales to 0
                                            │  Temporal + Firestore only, no PetKit
                                            ▼
                                     Temporal Cloud (us-west1)
                                            │  scales the pool via the invoker SA
                                            ▼
                                  petkit-worker-<build-id>  Cloud Run worker pool, 0–1 instances
                                            │  the only PetKit client
                                            ▼
                                  PetKit cloud ─► feeder
                         Firestore `petkit` (us-west1) ◄── snapshots ── worker
```

Everything lives in `us-west1`, the GCP region Temporal Cloud uses.

## Prerequisites

- **gcloud** with a Python that has `grpcio` (the worker-pool commands need it).
  If `gcloud run worker-pools --help` fails with `No module named 'grpc'`:
  ```bash
  # gcloud needs Python 3.10+; macOS's built-in python3 is 3.9, so name one
  # explicitly (e.g. Homebrew's python3.12)
  /opt/homebrew/bin/python3.12 -m venv ~/.gcloud-python
  ~/.gcloud-python/bin/pip install grpcio
  # add to your shell profile:
  export CLOUDSDK_PYTHON=~/.gcloud-python/bin/python
  export CLOUDSDK_PYTHON_SITEPACKAGES=1
  ```
- **Temporal CLI** 1.8.3 or later (`temporal --version`).
- **Terraform** (one-time, for the Temporal invoker service account).
- **`.env.cloud`**: copy `.env.cloud.example` and fill it in. It's gitignored.
  Secrets in it are only used locally, to create Secret Manager secrets and to
  run the Temporal CLI.

## 1. One-time Google Cloud setup

```bash
DRY_RUN=1 scripts/cloud-setup.sh   # preview
scripts/cloud-setup.sh
```

Enables APIs and creates the Artifact Registry repo `petkit`, the secrets
`temporal-api-key`, `petkit-username`, and `petkit-password`, and two
service accounts:

| Service account | Runs | Can access |
|---|---|---|
| `petkit-api` | API service | Firestore (read), `temporal-api-key` |
| `petkit-worker` | Worker pool ("runner") | Firestore (read/write), all three secrets |

To change a secret later, update `.env.cloud` and run
`ROTATE_SECRETS=1 scripts/cloud-setup.sh`.

## 2. Temporal invoker service account (one-time)

Temporal Cloud scales the worker pool by impersonating an **invoker** service
account in your project. It's separate from the runner (`petkit-worker`).

1. In the Temporal Cloud UI: **Workers → Create Worker Deployment → Access**,
   and copy the Terraform template. It has Temporal's impersonator accounts
   filled in.
2. Set `project_id = "petkit-temporal"` and
   `runner_service_account_email = "petkit-worker@petkit-temporal.iam.gserviceaccount.com"`.
3. `terraform init && terraform apply`
4. Put the `invoker_email` output in `.env.cloud` as `TEMPORAL_INVOKER_SA`.

## 3. Deploy

Commit first: the build ID is the git commit.

```bash
DRY_RUN=1 scripts/deploy.sh   # preview every command
scripts/deploy.sh
```

Each deploy builds the image, updates the API service, creates a new worker
pool `petkit-worker-<build-id>` at 0 instances, registers it as a Temporal
Worker Deployment Version (max 1 instance, because PetKit allows one session),
and makes it current.

## 4. Open the app

The API service is private (IAM only). Proxy it with your gcloud credentials:

```bash
gcloud run services proxy petkit-api --region us-west1 --port 8080
```

Then browse to <http://localhost:8080>. On first use:
1. Click **Load feeders from PetKit** if the list is empty.
2. Set the feeding schedule again. Schedules live in the Temporal namespace,
   and this is a new namespace.

## 5. Cut over from the laptop

Your local Temporal dev server still has its own feeding schedule. If both run,
the pet gets fed twice. After step 4:

1. Stop the local backend.
2. Delete the local schedule: `temporal schedule delete --schedule-id feeding-schedule-<device-id>`
   (against the local dev server), or stop the dev server.

The local backend and the cloud worker also share one PetKit account, and only
one session can be active. Don't run both at once.

## Watching it scale (good footage for the video)

```bash
# Instance count: 0 when idle, 1 while a feed or refresh runs
gcloud run worker-pools describe petkit-worker-<build-id> --region us-west1
gcloud run worker-pools logs read petkit-worker-<build-id> --region us-west1
```

The refresh schedule wakes the pool every 30 minutes. A manual feed or a settings
change wakes it within seconds. It scales back to 0 about 90 seconds after the
last task.

## Cleaning up old worker pools

Every deploy leaves the previous pool behind, serving workflows pinned to it.
Ours finish within minutes, so it's safe to remove old versions soon after a
deploy:

```bash
temporal worker deployment describe --name petkit               # list versions
temporal worker deployment delete-version --deployment-name petkit --build-id <old-id>
gcloud run worker-pools delete petkit-worker-<old-id> --region us-west1
```

`delete-version` refuses while the version still has pinned workflows running.

## Troubleshooting

- **Cloud Build permission errors** on a new project: the build's service
  account may need `roles/artifactregistry.writer` and `roles/logging.logWriter`.
  The error message names the account.
- **Workflows sit in "Running" with no progress**: check that the version is
  current (`temporal worker deployment describe --name petkit`), then use
  **Validate Connection** on the version in the Temporal Cloud UI. That checks
  impersonation but not the update permission scaling needs.
