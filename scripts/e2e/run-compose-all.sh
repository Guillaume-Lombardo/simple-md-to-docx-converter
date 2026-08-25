#!/usr/bin/env bash
set -euo pipefail

bash scripts/e2e/run-compose.sh
MARKWEAVE_SIMPLE_E2E_RUNTIME=docker bash scripts/e2e/run-compose-simple.sh
MARKWEAVE_SIMPLE_E2E_RUNTIME=podman bash scripts/e2e/run-compose-simple.sh
