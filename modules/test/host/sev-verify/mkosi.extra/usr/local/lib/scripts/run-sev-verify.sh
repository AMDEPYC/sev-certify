#!/usr/bin/bash
set -euo pipefail

RESULTS_DIR="/root/results"
LOG_FILE="${RESULTS_DIR}/sev-verify.log"

mkdir -p "$RESULTS_DIR"

# This is a dedicated test-host image (rebuilt/reprovisioned per run), so
# boot-session-only host changes are fine — notably letting `snphost commit`
# advance the committed TCB floor (it resets on reboot).
python3 -m sev_verify \
	/usr/local/lib/guest-image/guest.efi \
	--output-dir "$RESULTS_DIR" \
	--allow-host-changes \
	2>&1 | tee "$LOG_FILE"

exit "${PIPESTATUS[0]}"
