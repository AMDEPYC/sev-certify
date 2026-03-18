#!/bin/bash

BOOT_LOG_DIR="/var/log/journal/guest-logs"
GUEST_ERROR_LOG="/tmp/guest-error.log"

# Wait for the SNP Guest to boot up
TIMEOUT=60
INTERVAL=1
ELAPSED=0

while [[ $ELAPSED -lt $TIMEOUT ]]; do
    if journalctl -D "${BOOT_LOG_DIR}" 2>/dev/null | grep -q "boot-successful"; then
        echo "Guest boot successful."
        exit 0
    fi
    sleep $INTERVAL
    ELAPSED=$((ELAPSED + INTERVAL))
done

echo -e "ERROR: Timed out waiting for SNP Guest to signal successful boot.\n" >&2

# Show guest boot log errors if any
if [ -s "${GUEST_ERROR_LOG}" ]; then
    echo -e "QEMU error log:\n" >&2
    cat "${GUEST_ERROR_LOG}" >&2
fi

exit 2
