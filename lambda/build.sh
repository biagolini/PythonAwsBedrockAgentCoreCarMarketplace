#!/usr/bin/env bash
# Builds a deployment zip for each Lambda, dependencies bundled in, no Lambda
# Layer needed. Targets Lambda's arm64 architecture and Python 3.14, regardless
# of the machine this runs on, see
# https://docs.aws.amazon.com/lambda/latest/dg/python-package.html
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

FUNCTIONS=(search_cars)
DIST_DIR="dist"

mkdir -p "${DIST_DIR}"

for name in "${FUNCTIONS[@]}"; do
  echo "Building ${name}.zip"

  rm -rf "${name}/build" "${DIST_DIR}/${name}.zip"
  mkdir -p "${name}/build"

  pip3 install \
    --platform manylinux2014_aarch64 \
    --target "${name}/build" \
    --implementation cp \
    --python-version 3.14 \
    --only-binary=:all: \
    -r "${name}/requirements.txt"

  cp "${name}/lambda_function.py" "${name}/build/"

  find "${name}/build" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
  find "${name}/build" -type f -name "*.pyc" -delete 2>/dev/null || true

  (cd "${name}/build" && zip -r "../../${DIST_DIR}/${name}.zip" . -q)
  rm -rf "${name}/build"

  echo "Built ${DIST_DIR}/${name}.zip"
done
