#!/usr/bin/bash
set -euo pipefail

RESULTS_DIR="/root/results"
SEV_VERIFY_LOG="${RESULTS_DIR}/sev-verify.log"

build_beacon_body() {
  local md_file=$1
  local body_file
  body_file=$(mktemp)

  cat "$md_file" > "$body_file"
  if [ -f "$SEV_VERIFY_LOG" ]; then
    {
      echo ""
      echo "### sev-verify output"
      echo '```'
      cat "$SEV_VERIFY_LOG"
      echo '```'
    } >> "$body_file"
  fi

  echo "$body_file"
}

# Determine OS name and version
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS_NAME="${ID}"
    OS_VERSION="${VERSION_ID:-""}"

    # Initialize OS release with the OS VERSION_CODENAME if VERSION_ID is missing in /etc/os-release.
    if [[ -z "${OS_VERSION}" && -n "${VERSION_CODENAME:-}" ]]; then
        OS_VERSION="${VERSION_CODENAME:-}"
    fi

    OS_LABEL="${OS_NAME}-${OS_VERSION}"
else
    OS_NAME="$(uname -s)"
    OS_VERSION=""
    OS_LABEL="${OS_NAME}"
fi

# Fetch AMD processor model
PROC_LABEL=$(/usr/bin/python3 /usr/local/lib/scripts/get_processor_model.py series)

# Loop over each certification result JSON produced by sev-verify
shopt -s nullglob
json_files=("${RESULTS_DIR}"/cert-*.json)
if [ ${#json_files[@]} -eq 0 ]; then
    echo "No certification results found in ${RESULTS_DIR}" >&2
    exit 1
fi

for json_file in "${json_files[@]}"; do
  # Parse fields from sev-verify JSON output
  cert_version=$(jq -r '.certification_version' "$json_file")
  certified_level=$(jq -r '.certified_level // empty' "$json_file")  # null -> empty string

  # Corresponding markdown report
  md_file="${RESULTS_DIR}/cert-${cert_version}.md"
  if [ ! -f "$md_file" ]; then
    echo "Markdown report not found: ${md_file}" >&2
    exit 1
  fi

  # Build title
  if [ -n "$OS_VERSION" ]; then
    SEV_TITLE="${OS_NAME} ${OS_VERSION} SEV version ${cert_version}"
  else
    SEV_TITLE="${OS_NAME} SEV version ${cert_version}"
  fi

  # Set up parameters
  PARAMS=()

  # Add labels
  PARAMS+=("--label" "certificate")
  PARAMS+=("--label" "os-${OS_LABEL}")
  PARAMS+=("--label" "proc-${PROC_LABEL}")

  # Add milestone for max achieved certification level
  if [ -n "$certified_level" ]; then
    PARAMS+=("--milestone" "c${certified_level}")
  fi

  body_file=$(build_beacon_body "$md_file")

  beacon report --title "$SEV_TITLE" --body "$body_file" "${PARAMS[@]}"
  rm -f "$body_file"

  echo "Published SEV certificate via beacon with title: $SEV_TITLE"
done
