#!/bin/bash

set -euo pipefail

EFI_PATH="/usr/local/lib/guest-image/guest.efi"
MEASUREMENT_FILE="/usr/local/lib/guest-image/guest_measurement.txt"
ID_BLOCK_FILE="/usr/local/lib/guest-image/id-block.b64"
ID_AUTH_FILE="/usr/local/lib/guest-image/id-auth.b64"
GUEST_ERROR_LOG="/tmp/guest-error.log"
EXTRA_QEMU_OPTS="${EXTRA_QEMU_OPTS:-}"

# Verbose mode: -v flag or LAUNCH_GUEST_VERBOSE=1 env var
VERBOSE="${LAUNCH_GUEST_VERBOSE:-0}"
while getopts "v" opt; do
    case $opt in
        v) VERBOSE=1 ;;
        *) echo "Usage: $0 [-v]" >&2; exit 1 ;;
    esac
done

dbg() { [ "${VERBOSE}" -eq 1 ] && echo "[debug] $*" || true; }

# Check which OVMF binary to use
OVMF_PATH=""
for path in /usr/share/ovmf/OVMF.amdsev.fd /usr/share/edk2/ovmf/OVMF.amdsev.fd; do
  if [ -f "${path}" ]; then
    OVMF_PATH="${path}"
    break
  fi
done

if [ -z "${OVMF_PATH}" ] || [ ! -f "${OVMF_PATH}" ]; then
    echo "ERROR: AMDSEV compatible OVMF is not present, can't launch SEV enabled guest" >&2
    exit 1
fi
dbg "OVMF: ${OVMF_PATH}"
dbg "EFI:  ${EFI_PATH}"

# Convert measurement to the appropriate sha format to pass in as host data
calculated_measurement_hex=$(awk -F "0x" '{print $2}' "${MEASUREMENT_FILE}")
guest_measurement_sha256sum=$(echo "${calculated_measurement_hex}" | sha256sum | cut -d ' ' -f 1 | xxd -r -p | base64)
dbg "Measurement (hex):    ${calculated_measurement_hex}"
dbg "Measurement (sha256): ${guest_measurement_sha256sum}"

# Build sev-snp-guest object; append ID block args if files are present
SEV_SNP_OBJECT="sev-snp-guest,id=sev0,cbitpos=51,reduced-phys-bits=1,kernel-hashes=on,host-data=${guest_measurement_sha256sum}"
if [ -f "${ID_BLOCK_FILE}" ] && [ -f "${ID_AUTH_FILE}" ]; then
    ID_BLOCK_B64=$(cat "${ID_BLOCK_FILE}")
    ID_AUTH_B64=$(cat "${ID_AUTH_FILE}")
    # Extract policy from id-block (bytes 88-95, LE u64) so LAUNCH_START and
    # LAUNCH_FINISH see the same value; without this QEMU uses its own default.
    POLICY=$(base64 -d "${ID_BLOCK_FILE}" | python3 -c \
        "import sys; d=sys.stdin.buffer.read(); print(hex(int.from_bytes(d[88:96],'little')))")
    SEV_SNP_OBJECT="${SEV_SNP_OBJECT},policy=${POLICY},id-block=${ID_BLOCK_B64},id-auth=${ID_AUTH_B64}"
    dbg "ID block: ${ID_BLOCK_FILE} (present)"
    dbg "ID auth:  ${ID_AUTH_FILE} (present)"
    dbg "Policy:   ${POLICY} (from id-block)"
else
    dbg "ID block files not found — launching without ID block"
    dbg "  checked: ${ID_BLOCK_FILE}"
    dbg "  checked: ${ID_AUTH_FILE}"
fi
dbg "sev-snp-guest object: ${SEV_SNP_OBJECT}"

# Clean up the error trace before QEMU guest launch
truncate -s 0 "${GUEST_ERROR_LOG}"

echo -e "\nSNP Guest boot is in progress ..."

QEMU_CMD=(
    qemu-system-x86_64
    -enable-kvm
    -machine q35
    -cpu EPYC-v4
    -machine memory-encryption=sev0
    -monitor none
    -display none
    -object memory-backend-memfd,id=ram1,size=2048M
    -machine memory-backend=ram1
    -object "${SEV_SNP_OBJECT}"
    -bios "${OVMF_PATH}"
    -kernel "${EFI_PATH}"
)

# Append any extra QEMU options (word-split intentionally)
# shellcheck disable=SC2206
[ -n "${EXTRA_QEMU_OPTS}" ] && QEMU_CMD+=(${EXTRA_QEMU_OPTS})

if [ "${VERBOSE}" -eq 1 ]; then
    echo "[debug] QEMU command:"
    printf "[debug]   %s\n" "${QEMU_CMD[@]}"
fi

exec "${QEMU_CMD[@]}" 2> "${GUEST_ERROR_LOG}"
