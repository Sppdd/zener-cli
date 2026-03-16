#!/usr/bin/env bash
# deploy.sh — Infrastructure-as-code deployment script for Zener Server
#
# Provisions and deploys the Zener Cloud Run backend from scratch.
# Idempotent: safe to run repeatedly; existing resources are reused.
#
# Usage:
#   ./deploy.sh                        # uses defaults below
#   PROJECT_ID=my-project ./deploy.sh  # override project
#   REGION=us-east1 ./deploy.sh        # override region
#
# Prerequisites:
#   - gcloud CLI installed and logged in  (gcloud auth login)
#   - Docker installed (for local builds; skipped when using Cloud Build)
#   - Billing enabled on the GCP project

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────

PROJECT_ID="${PROJECT_ID:-zener-ai-hackathon}"
REGION="${REGION:-us-central1}"
SERVICE_NAME="${SERVICE_NAME:-zener-server}"
SERVICE_ACCOUNT="${SERVICE_ACCOUNT:-zener-server-sa}"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}:latest"

# Cloud Run tuning
MEMORY="${MEMORY:-2Gi}"
CPU="${CPU:-2}"
MAX_INSTANCES="${MAX_INSTANCES:-10}"
CONCURRENCY="${CONCURRENCY:-1}"
TIMEOUT="${TIMEOUT:-300}"

# ── Helpers ───────────────────────────────────────────────────────────────────

info()    { echo "  [info]  $*"; }
success() { echo "  [ok]    $*"; }
warn()    { echo "  [warn]  $*"; }
die()     { echo "  [error] $*" >&2; exit 1; }

require() {
  command -v "$1" >/dev/null 2>&1 || die "'$1' is required but not installed."
}

# ── Preflight ─────────────────────────────────────────────────────────────────

echo ""
echo "  Zener Server — Cloud Deployment"
echo "  ─────────────────────────────────────────────────────"
echo "  project  : ${PROJECT_ID}"
echo "  region   : ${REGION}"
echo "  service  : ${SERVICE_NAME}"
echo "  image    : ${IMAGE}"
echo "  ─────────────────────────────────────────────────────"
echo ""

require gcloud

# Set active project
gcloud config set project "${PROJECT_ID}" --quiet
info "Active project: ${PROJECT_ID}"

# ── Step 1: Enable required APIs ──────────────────────────────────────────────

info "Enabling required GCP APIs..."

gcloud services enable \
  cloudbuild.googleapis.com \
  run.googleapis.com \
  containerregistry.googleapis.com \
  aiplatform.googleapis.com \
  iam.googleapis.com \
  --project="${PROJECT_ID}" \
  --quiet

success "APIs enabled."

# ── Step 2: Create service account (idempotent) ───────────────────────────────

SA_EMAIL="${SERVICE_ACCOUNT}@${PROJECT_ID}.iam.gserviceaccount.com"

if gcloud iam service-accounts describe "${SA_EMAIL}" \
     --project="${PROJECT_ID}" >/dev/null 2>&1; then
  info "Service account already exists: ${SA_EMAIL}"
else
  info "Creating service account: ${SA_EMAIL}"
  gcloud iam service-accounts create "${SERVICE_ACCOUNT}" \
    --display-name="Zener Server Runtime" \
    --project="${PROJECT_ID}"
  success "Service account created."
fi

# ── Step 3: Grant IAM roles ───────────────────────────────────────────────────

info "Granting IAM roles to service account..."

ROLES=(
  "roles/aiplatform.user"          # Vertex AI inference
  "roles/logging.logWriter"        # Cloud Logging
  "roles/monitoring.metricWriter"  # Cloud Monitoring
  "roles/storage.objectViewer"     # GCS read (for model artifacts)
)

for ROLE in "${ROLES[@]}"; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="${ROLE}" \
    --condition=None \
    --quiet >/dev/null
  info "  granted ${ROLE}"
done

success "IAM roles granted."

# ── Step 4: Build and push container image ────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

info "Submitting container build to Cloud Build..."
gcloud builds submit "${SCRIPT_DIR}" \
  --tag="${IMAGE}" \
  --project="${PROJECT_ID}" \
  --quiet

success "Image built and pushed: ${IMAGE}"

# ── Step 5: Deploy to Cloud Run ───────────────────────────────────────────────

info "Deploying to Cloud Run..."

gcloud run deploy "${SERVICE_NAME}" \
  --image="${IMAGE}" \
  --region="${REGION}" \
  --platform=managed \
  --allow-unauthenticated \
  --service-account="${SA_EMAIL}" \
  --memory="${MEMORY}" \
  --cpu="${CPU}" \
  --max-instances="${MAX_INSTANCES}" \
  --concurrency="${CONCURRENCY}" \
  --timeout="${TIMEOUT}" \
  --port=8080 \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=${REGION},GOOGLE_GENAI_USE_VERTEXAI=true" \
  --project="${PROJECT_ID}" \
  --quiet

# ── Step 6: Print service URL ─────────────────────────────────────────────────

SERVICE_URL=$(gcloud run services describe "${SERVICE_NAME}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --format="value(status.url)")

echo ""
echo "  ─────────────────────────────────────────────────────"
success "Deployment complete."
echo ""
echo "  Service URL : ${SERVICE_URL}"
echo "  Health check: ${SERVICE_URL}/health"
echo ""
echo "  Point the CLI at this server:"
echo "    export ZENER_SERVER_URL=${SERVICE_URL}"
echo "  or save it permanently:"
echo "    zener setup"
echo "  ─────────────────────────────────────────────────────"
echo ""
