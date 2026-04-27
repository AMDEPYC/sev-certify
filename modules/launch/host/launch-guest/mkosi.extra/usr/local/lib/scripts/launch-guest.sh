#!/bin/bash

set -euo pipefail

EFI_PATH="/usr/local/lib/guest-image/guest.efi"
MEASUREMENT_FILE="/usr/local/lib/guest-image/guest_measurement.txt"
GUEST_ERROR_LOG="/tmp/guest-error.log"
GUEST_BOOT_LOG="/tmp/guest_boot.log"
# Check which OVMF binary to use
OVMF_PATH=""
for path in /usr/share/ovmf/OVMF.amdsev.fd /usr/share/edk2/ovmf/OVMF.amdsev.fd; do
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

echo -e "\nSNP Guest boot is in progress ..."

# Launch the SNP guest in background
exec qemu-system-x86_64 \
  -enable-kvm \
  -machine q35 \
  -cpu EPYC-v4 \
  -netdev user,id=net0 \
  -device e1000,netdev=net0 \
  -monitor none \
  -display none \
  -machine memory-encryption=sev0 \
  -object memory-backend-memfd,id=ram1,size=2048M \
  -machine memory-backend=ram1 \
  -object sev-snp-guest,id=sev0,cbitpos=51,reduced-phys-bits=1,kernel-hashes=on,host-data="${guest_measurement_sha256sum}" \
  -bios ${OVMF_PATH} \
  -serial file:${GUEST_BOOT_LOG} \
  -kernel ${EFI_PATH} 2> ${GUEST_ERROR_LOG}
