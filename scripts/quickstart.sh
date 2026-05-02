#!/bin/bash
# Quickstart script for running a single SWE-bench Pro instance.
# Usage: ./scripts/quickstart.sh <instance_id> <n>
# Example: ./scripts/quickstart.sh astropy__astropy-14539 3

set -euo pipefail

INSTANCE_ID="${1:-}"
PLAN_COUNT="${2:-3}"

# ------------------------------------------------------------------
# Validate arguments
# ------------------------------------------------------------------
if [[ -z "$INSTANCE_ID" ]]; then
    echo "Error: instance_id is required."
    echo "Usage: $0 <instance_id> <n>"
    echo "Example: $0 astropy__astropy-14539 3"
    exit 1
fi

if ! [[ "$PLAN_COUNT" =~ ^[0-9]+$ ]] || [[ "$PLAN_COUNT" -lt 1 ]]; then
    echo "Error: n must be a positive integer, got '$PLAN_COUNT'"
    exit 1
fi

# ------------------------------------------------------------------
# Validate environment
# ------------------------------------------------------------------
if [[ -z "${DEEPSEEK_API_KEY:-}" ]]; then
    echo "Error: DEEPSEEK_API_KEY environment variable is not set."
    echo "Please set it: export DEEPSEEK_API_KEY='your-key'"
    exit 1
fi

if ! command -v conda &> /dev/null; then
    echo "Warning: conda not found. Make sure 'mini-swe' environment is available."
fi

if ! command -v docker &> /dev/null; then
    echo "Error: Docker is not installed or not in PATH."
    exit 1
fi

echo "[1/5] Instance: $INSTANCE_ID, Plan count: $PLAN_COUNT"

# ------------------------------------------------------------------
# Activate conda environment
# ------------------------------------------------------------------
echo "[2/5] Activating conda environment 'mini-swe'..."
if command -v conda &> /dev/null; then
    eval "$(conda shell.bash hook)"
    conda activate mini-swe || {
        echo "Error: Failed to activate conda environment 'mini-swe'"
        exit 1
    }
else
    echo "Warning: Skipping conda activation (conda not found)"
fi

# ------------------------------------------------------------------
# Install Python dependencies
# ------------------------------------------------------------------
echo "[3/5] Installing Python dependencies..."
pip install -q -r requirements.txt

# ------------------------------------------------------------------
# Build SWE-bench Pro Docker images
# ------------------------------------------------------------------
echo "[4/5] Building SWE-bench Pro Docker images..."
if [[ -x "./scripts/build_docker_images.sh" ]]; then
    ./scripts/build_docker_images.sh --instance "$INSTANCE_ID"
else
    echo "Warning: build_docker_images.sh not found or not executable."
    echo "Skipping Docker image build (assuming images already exist)."
fi

# ------------------------------------------------------------------
# Run the pipeline
# ------------------------------------------------------------------
echo "[5/5] Running plan-code-test pipeline..."
mkdir -p "./output"

python -m src.main \
    --config config.yaml \
    --instance "$INSTANCE_ID" \
    --n "$PLAN_COUNT" \
    --output-dir "./output"

echo "Done. Results saved to ./output/$INSTANCE_ID/"
