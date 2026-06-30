#!/usr/bin/bash
set -uo pipefail

RESULTS_DIR="/root/results"
LOG_FILE="${RESULTS_DIR}/sev-verify.log"

mkdir -p "$RESULTS_DIR"

python3 -m sev_verify \
	/usr/local/lib/guest-image/guest.efi \
	--output-dir "$RESULTS_DIR" \
	2>&1 | tee "$LOG_FILE"

exit "${PIPESTATUS[0]}"
