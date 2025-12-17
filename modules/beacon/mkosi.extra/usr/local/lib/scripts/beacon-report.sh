#!/usr/bin/bash
set -euo pipefail

SEV_VERSIONS=("3.0-0")
SEV_CERT_FILE=""

# Temporarily hardcode the milestone name
MILESTONE="c3.0.0-0"

# Determine OS name and version
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS_NAME="${ID}"            
    OS_VERSION="${VERSION_ID:-""}"

    # Initialize OS release with the OS VERSION_CODENAME if VERSION_ID is missing in /etc/os-release.
    if [[ -z "${OS_VERSION}" && -n "${VERSION_CODENAME}" ]]; then
        OS_VERSION="${VERSION_CODENAME}"
    fi

    OS_LABEL="${OS_NAME}-${OS_VERSION}"
else
    OS_NAME="$(uname -s)"
    OS_VERSION=""
    OS_LABEL="${OS_NAME}"
fi

# Fetch AMD processor model
PROC_LABEL=$(/usr/bin/python3 /usr/local/bin/scripts/get_processor_model.py series)

# Loop over to generate beacon report for all SEV certificates
for sev_version in "${SEV_VERSIONS[@]}"; do
  # Build title
  if [ -n "$OS_VERSION" ]; then
    SEV_TITLE="${OS_NAME} ${OS_VERSION} SEV version ${sev_version}"
  else
    SEV_TITLE="${OS_NAME} SEV version ${sev_version}"
  fi

  # Obtain SEV Version Content
  SEV_CERT_FILE="${HOME:-/root}/sev_certificate_v${sev_version}.txt"

  # Set up parameters
  PARAMS=()

  # Add labels
  PARAMS+=("--label" "certificate")
  PARAMS+=("--label" "os-${OS_LABEL}")
  PARAMS+=("--label" "proc-${PROC_LABEL}")

  # Add milestone for valid test
  if [ -e "${SEV_CERT_FILE}" ] && [ -z "$(grep "❌" "${SEV_CERT_FILE}")" ]; then
    PARAMS+=("--milestone" "$MILESTONE")
  fi

  beacon report --title "$SEV_TITLE" --body "$SEV_CERT_FILE" "${PARAMS[@]}"

  echo "Published SEV certificate via beacon with title: $SEV_TITLE"
done
