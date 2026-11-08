# Terraform GCP Service Account Module for Cloud Run Serverless Workers.
locals {
  temporal_cloud_root_account_id = "hmmf1"
}

module "serverless-worker-cloud-run" {
  source = "github.com/temporalio/terraform-modules//modules/serverless-workers/gcp/cloud-run"

  project_id         = "petkit-temporal"
  invoker_account_id = "temporal-worker-pool-invoker"

  # Required: the dedicated service account the worker pool runs as. Create it
  # yourself, set it as the pool's service account (spec.template.serviceAccount),
  # and grant it the least-privilege roles the pool needs:
  #   - roles/monitoring.metricWriter       (metrics -> Managed Service for Prometheus)
  #   - roles/telemetry.tracesWriter        (traces  -> Telemetry API)
  #   - roles/secretmanager.secretAccessor  (on each secret the pool injects)
  #
  # Ours is petkit-worker, created by scripts/cloud-setup.sh with Firestore and
  # secret access. The metrics/traces roles above are for an OpenTelemetry
  # collector sidecar, which this app doesn't run.
  runner_service_account_email = "petkit-worker@petkit-temporal.iam.gserviceaccount.com"

  impersonator_service_account_emails = [
    "serverless-${local.temporal_cloud_root_account_id}@prod-vs4padk9dkczcey6d6st9c58e.iam.gserviceaccount.com",
    "serverless-${local.temporal_cloud_root_account_id}@prod-sbe56h6wr4sykaw47a6yg4h66.iam.gserviceaccount.com",
    "serverless-${local.temporal_cloud_root_account_id}@prod-97gr6tp6zkv2t4epubffpz7kt.iam.gserviceaccount.com",
    "serverless-${local.temporal_cloud_root_account_id}@prod-c4cqaqgbfzy23vpzkbxzrx378.iam.gserviceaccount.com",
    "serverless-${local.temporal_cloud_root_account_id}@prod-79z2c6d76au6g5fcq768buqf9.iam.gserviceaccount.com",
  ]
}

# Once applied, `terraform output invoker_email` prints the invoker's email.
# Put it in .env.cloud as TEMPORAL_INVOKER_SA; scripts/deploy.sh passes it to
# Temporal Cloud when registering each worker version.
output "invoker_email" {
  value = module.serverless-worker-cloud-run.invoker_email
}
