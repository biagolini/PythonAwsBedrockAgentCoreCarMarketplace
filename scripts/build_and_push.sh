#!/usr/bin/env bash
# Build the agent image for linux/arm64 and push it to Amazon ECR tagged
# `latest`, creating the repository on the first run. Same result as the
# GitHub Actions workflow, for when you want to push from your machine.
#
# Usage:
#   AWS_PROFILE=<your-profile> ./scripts/build_and_push.sh
# Optional overrides: AWS_REGION, REPOSITORY, IMAGE_TAG
set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-1}"
REPOSITORY="${REPOSITORY:-agentcore/dealership-assistant-agent}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
IMAGE="${REGISTRY}/${REPOSITORY}:${IMAGE_TAG}"

aws ecr get-login-password --region "${AWS_REGION}" \
  | docker login --username AWS --password-stdin "${REGISTRY}"

if ! aws ecr describe-repositories --repository-names "${REPOSITORY}" --region "${AWS_REGION}" >/dev/null 2>&1; then
  echo "Creating ECR repository ${REPOSITORY}"
  aws ecr create-repository --repository-name "${REPOSITORY}" --region "${AWS_REGION}" >/dev/null
fi

echo "Building ${IMAGE}"
# --provenance=false --sbom=false keep the push to a single image manifest:
# without them, buildx also pushes an attestation manifest and wraps everything
# in an image index, which shows up as extra untagged entries in ECR.
docker buildx build --platform linux/arm64 \
  --provenance=false --sbom=false \
  --tag "${IMAGE}" --push "${ROOT_DIR}/agent"
echo "Pushed ${IMAGE}"
