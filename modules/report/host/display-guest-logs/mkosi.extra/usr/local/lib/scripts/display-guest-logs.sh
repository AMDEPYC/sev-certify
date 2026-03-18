#!/bin/bash

GUEST_JOURNAL_LOCATION="/var/log/journal/guest-logs"

# Wait for the SNP Guest to boot up
TIMEOUT=60
INTERVAL=1
ELAPSED=0

units=("snpguest-ok.service" "attestation-workflow.service")

args=()
for unit in "${units[@]}"; do
  args+=(-u "$unit")
done

while [[ $ELAPSED -lt $TIMEOUT ]]; do
    if journalctl -D ${GUEST_JOURNAL_LOCATION} | grep -q "Guest Tests Completed"; then
        echo -e "\nSEV-SNP guest test results:"
        echo -e "\nFor more information check journals in ${GUEST_JOURNAL_LOCATION}"

        # Get the guest logs with the appropriate services
        guest_service_log=$(journalctl -D "${GUEST_JOURNAL_LOCATION}" "${args[@]}" -o cat)

        # Add SNP guest's snpguest-attestation service log into the host's launch-guest service
        echo -e "\n${guest_service_log}"
        exit 0
    fi
    sleep $INTERVAL
    ELAPSED=$((ELAPSED + INTERVAL))
done

# If timeout hits but logs are there, then show the logs.
guest_service_log=$(journalctl -D "${GUEST_JOURNAL_LOCATION}" "${args[@]}" -o cat)

if [[ -n "$guest_service_log" ]]; then
    echo -e "\nSEV-SNP guest test results:"
    echo -e "\nFor more information check journals in ${GUEST_JOURNAL_LOCATION}"

    # Get the guest logs with the appropriate services
    guest_service_log=$(journalctl -D "${GUEST_JOURNAL_LOCATION}" "${args[@]}" -o cat)

    # Add SNP guest's snpguest-attestation service log into the host's launch-guest service
    echo -e "\n${guest_service_log}"
    exit 0
else
    echo -e "\"${GUEST_JOURNAL_LOCATION}\" guest journal directory is empty!!"
    echo -e "systemd-journal-upload.service may not be active and running on the guest"
    echo -e "Please check for the systemd-journal-upload.service status on the guest"
fi

exit 2
