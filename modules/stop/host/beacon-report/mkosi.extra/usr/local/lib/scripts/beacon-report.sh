#!/usr/bin/bash
set -euo pipefail

# Maximum milestone version to report via beacon.
# Milestones above this version are omitted from the report even if achieved.
# Bump this when a new certification level is ready to be officially reported.
MAX_MILESTONE="3.0.0-0"

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
PROC_LABEL=$(/usr/bin/python3 /usr/local/lib/scripts/get_processor_model.py series)

# Find the combined certificate file (generator names it after the highest achieved level)
SEV_CERT_FILE=$(compgen -G "${HOME:-/root}/sev_certificate_v3.0.*.txt" | sort -V | tail -1 || true)
if [ -z "$SEV_CERT_FILE" ]; then
  # Fallback: no level achieved, generator writes unversioned file
  SEV_CERT_FILE="${HOME:-/root}/sev_certificate.txt"
fi

# Extract achieved version from filename (e.g. sev_certificate_v3.0.0-1.txt -> 3.0.0-1)
ACHIEVED=$(basename "$SEV_CERT_FILE" | sed -n 's/sev_certificate_v\(.*\)\.txt/\1/p')

# Cap achieved version to MAX_MILESTONE for reporting purposes
REPORTED="$ACHIEVED"
if [ -n "$ACHIEVED" ]; then
  if [ "$(printf '%s\n' "$MAX_MILESTONE" "$ACHIEVED" | sort -V | head -1)" != "$ACHIEVED" ]; then
    echo "Achieved milestone c${ACHIEVED} exceeds MAX_MILESTONE ${MAX_MILESTONE}, capping to c${MAX_MILESTONE}"
    REPORTED="$MAX_MILESTONE"
  fi
fi

# Build title
if [ -n "$OS_VERSION" ]; then
  SEV_TITLE="${OS_NAME} ${OS_VERSION} SEV certification${REPORTED:+ v${REPORTED}}"
else
  SEV_TITLE="${OS_NAME} SEV certification${REPORTED:+ v${REPORTED}}"
fi

# Set up parameters
PARAMS=()
PARAMS+=("--label" "certificate")
PARAMS+=("--label" "os-${OS_LABEL}")
PARAMS+=("--label" "proc-${PROC_LABEL}")

# Add milestone if a level was achieved
if [ -n "$REPORTED" ]; then
  PARAMS+=("--milestone" "c${REPORTED}")
fi

beacon report --title "$SEV_TITLE" --body "$SEV_CERT_FILE" "${PARAMS[@]}"

echo "Published SEV certificate via beacon with title: $SEV_TITLE"
