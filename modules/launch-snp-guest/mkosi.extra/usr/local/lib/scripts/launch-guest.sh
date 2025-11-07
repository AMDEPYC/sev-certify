#!/bin/bash

set -euo pipefail

EFI_PATH="/usr/local/lib/guest-image/guest.efi"
MEASUREMENT_FILE="/usr/local/lib/guest-image/guest_measurement.txt"
GUEST_ERROR_LOG="/tmp/guest-error.log"

# Check which OVMF binary to use
OVMF_PATH=""
for path in /usr/share/ovmf/OVMF.amdsev.fd /usr/share/edk2/ovmf/OVMF.amdsev.fd /usr/share/qemu/ovmf-x86_64-sev.bin; do
  if [ -f "${path}" ]; then
    OVMF_PATH="${path}"
    break
  fi
done

if  [ -z "${OVMF_PATH}"  ] || [ ! -f "${OVMF_PATH}" ]; then
    echo "ERROR: AMDSEV compatible OVMF is not present, can't launch SEV enabled guest" >&2
    exit 1
fi


# Convert Measurement to the appropriate sha format to pass in as host data
calculated_measurement_hex=$(awk -F "0x" '{print $2}' "${MEASUREMENT_FILE}" )
guest_measurement_sha256sum=$(echo "${calculated_measurement_hex}" | sha256sum | cut -d ' ' -f 1 | xxd -r -p | base64 )

# Clean up the error trace before QEMU guest launch
truncate -s 0 ${GUEST_ERROR_LOG}

# Launch the SNP guest in background
exec qemu-system-x86_64 \
  -enable-kvm \
  -machine q35 \
  -cpu EPYC-v4 \
  -machine memory-encryption=sev0 \
  -monitor none \
  -display none \
  -object memory-backend-memfd,id=ram1,size=2048M \
  -machine memory-backend=ram1 \
  -object sev-snp-guest,id=sev0,cbitpos=51,reduced-phys-bits=1,kernel-hashes=on,host-data="${guest_measurement_sha256sum}" \
  -bios ${OVMF_PATH} \
  -kernel ${EFI_PATH} 2> ${GUEST_ERROR_LOG}

echo -e "\nSNP Guest boot is in progress ..."