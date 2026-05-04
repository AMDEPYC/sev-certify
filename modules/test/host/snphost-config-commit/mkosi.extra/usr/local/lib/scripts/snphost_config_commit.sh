#!/bin/bash

# snphost config/commit test script -- certification level 3.0.0-1
#
# Exercises snphost config set, config reset, and commit subcommands.
# Runs all 8 test cases, continuing past failures to collect full results.
#
# Output: JSON lines to stdout (captured by journald via StandardOutput=journal+console).
# Each test emits a {"type":"step"} line; a {"type":"summary"} line is emitted at the end.
# The service unit tags output with LogExtraFields:
#   SEV_VERSION=3.0.0-1        -- certification level
#   SEV_TEST_GROUP=snphost-config-commit  -- this test group's identifier

PASSED=0
FAILED=0

emit_step() {
  local test_name="$1" status="$2" detail="${3:-}"
  jq -nc --arg t "$test_name" --arg s "$status" --arg d "$detail" \
    '{type:"step",test:$t,status:$s} + (if $d!="" then {detail:$d} else {} end)'
  case "$status" in
    pass) PASSED=$((PASSED + 1)) ;;
    fail) FAILED=$((FAILED + 1)) ;;
  esac
}

emit_summary() {
  local overall="pass"
  [[ "$FAILED" -gt 0 ]] && overall="fail"
  jq -nc --arg s "$overall" --argjson p "$PASSED" --argjson f "$FAILED" \
    '{type:"summary",status:$s,passed:$p,failed:$f}'
}

run_cmd() {
  CMD_ERROR=""
  local output
  output=$("$@" 2>&1)
  local rc=$?
  if [[ $rc -ne 0 ]]; then
    CMD_ERROR="${output}"
    return 1
  fi
  echo "${output}"
}

# ─── TCB Parsing ────────────────────────────────────────────────────────────

parse_tcb_field() {
  local tcb_output="$1" field_name="$2" section="$3"
  echo "${tcb_output}" \
    | awk -v section="${section}" -v field="${field_name}" '
      BEGIN { in_section=0 }
      $0 ~ section " TCB:" { in_section=1; next }
      in_section && / TCB:/ { in_section=0 }
      in_section && $0 ~ field ":" {
        sub(/.*:[ \t]*/, "")
        gsub(/[ \t\r\n]/, "")
        print
        exit
      }
    '
}

read_platform_tcb() {
  local tcb_output="$1"
  PLATFORM_BL=$(parse_tcb_field "${tcb_output}" "Boot Loader" "Platform")
  PLATFORM_TEE=$(parse_tcb_field "${tcb_output}" "TEE" "Platform")
  PLATFORM_SNP=$(parse_tcb_field "${tcb_output}" "SNP" "Platform")
  PLATFORM_UCODE=$(parse_tcb_field "${tcb_output}" "Microcode" "Platform")
  PLATFORM_FMC=$(parse_tcb_field "${tcb_output}" "FMC" "Platform")

  if [[ -n "${PLATFORM_FMC}" ]]; then
    HAS_FMC=1
    [[ "${PLATFORM_FMC}" == "None" ]] && PLATFORM_FMC=0
  else
    HAS_FMC=0
  fi
}

build_config_set_args() {
  local bl="$1" tee="$2" snp="$3" ucode="$4" mask="$5"
  CONFIG_ARGS=("${bl}" "${tee}" "${snp}" "${ucode}")
  [[ "${HAS_FMC}" -eq 1 ]] && CONFIG_ARGS+=("${PLATFORM_FMC}")
  CONFIG_ARGS+=("${mask}")
}

tcb_versions_match() {
  local tcb_output="$1"
  local field reported platform
  for field in "Boot Loader" "TEE" "SNP" "Microcode"; do
    reported=$(parse_tcb_field "${tcb_output}" "${field}" "Reported")
    platform=$(parse_tcb_field "${tcb_output}" "${field}" "Platform")
    [[ "${reported}" != "${platform}" ]] && return 1
  done
  return 0
}

# ─── Tests ────────────────────────────────────────────────────────────────

test_read_tcb() {
  local output
  output=$(run_cmd snphost show tcb) || { emit_step "read_tcb" "fail" "${CMD_ERROR}"; return 1; }
  read_platform_tcb "${output}"
  local detail="bl=${PLATFORM_BL} tee=${PLATFORM_TEE} snp=${PLATFORM_SNP} ucode=${PLATFORM_UCODE}"
  [[ "${HAS_FMC}" -eq 1 ]] && detail+=" fmc=${PLATFORM_FMC}"
  emit_step "read_tcb" "pass" "${detail}"
}

test_config_set_lower() {
  local set_bl="${PLATFORM_BL}" set_tee="${PLATFORM_TEE}"
  local set_snp="${PLATFORM_SNP}" set_ucode="${PLATFORM_UCODE}"
  local decremented_field=""

  if [[ "${PLATFORM_BL}" -gt 0 ]]; then
    set_bl=$((PLATFORM_BL - 1)); decremented_field="Boot Loader"
  elif [[ "${PLATFORM_SNP}" -gt 0 ]]; then
    set_snp=$((PLATFORM_SNP - 1)); decremented_field="SNP"
  elif [[ "${PLATFORM_TEE}" -gt 0 ]]; then
    set_tee=$((PLATFORM_TEE - 1)); decremented_field="TEE"
  elif [[ "${PLATFORM_UCODE}" -gt 0 ]]; then
    set_ucode=$((PLATFORM_UCODE - 1)); decremented_field="Microcode"
  else
    emit_step "config_set_lower" "skip" "all platform TCB fields are 0"
    return 0
  fi

  build_config_set_args "${set_bl}" "${set_tee}" "${set_snp}" "${set_ucode}" 0
  run_cmd snphost config set "${CONFIG_ARGS[@]}" >/dev/null || { emit_step "config_set_lower" "fail" "${CMD_ERROR}"; return 1; }

  local verify_output
  verify_output=$(run_cmd snphost show tcb) || { emit_step "config_set_lower" "fail" "${CMD_ERROR}"; return 1; }

  local reported platform
  reported=$(parse_tcb_field "${verify_output}" "${decremented_field}" "Reported")
  platform=$(parse_tcb_field "${verify_output}" "${decremented_field}" "Platform")

  if [[ "${reported}" != "${platform}" ]]; then
    emit_step "config_set_lower" "pass" "${decremented_field}: ${platform}->${reported}"
  else
    emit_step "config_set_lower" "fail" "reported ${decremented_field} should differ from platform after config set"
    return 1
  fi
}

test_config_reset() {
  run_cmd snphost config reset >/dev/null || { emit_step "config_reset" "fail" "${CMD_ERROR}"; return 1; }
  local verify_output
  verify_output=$(run_cmd snphost show tcb) || { emit_step "config_reset" "fail" "${CMD_ERROR}"; return 1; }
  if tcb_versions_match "${verify_output}"; then
    emit_step "config_reset" "pass"
  else
    emit_step "config_reset" "fail" "reported TCB should match platform TCB after reset"
    return 1
  fi
}

test_mask_chip_id() {
  build_config_set_args "${PLATFORM_BL}" "${PLATFORM_TEE}" "${PLATFORM_SNP}" "${PLATFORM_UCODE}" 1
  run_cmd snphost config set "${CONFIG_ARGS[@]}" >/dev/null || { emit_step "mask_chip_id" "fail" "${CMD_ERROR}"; return 1; }
  run_cmd snphost show tcb >/dev/null || { emit_step "mask_chip_id" "fail" "${CMD_ERROR}"; return 1; }
  emit_step "mask_chip_id" "pass"
}

test_mask_chip_key() {
  build_config_set_args "${PLATFORM_BL}" "${PLATFORM_TEE}" "${PLATFORM_SNP}" "${PLATFORM_UCODE}" 2
  run_cmd snphost config set "${CONFIG_ARGS[@]}" >/dev/null || { emit_step "mask_chip_key" "fail" "${CMD_ERROR}"; return 1; }
  run_cmd snphost show tcb >/dev/null || { emit_step "mask_chip_key" "fail" "${CMD_ERROR}"; return 1; }
  emit_step "mask_chip_key" "pass"
}

test_both_masks() {
  build_config_set_args "${PLATFORM_BL}" "${PLATFORM_TEE}" "${PLATFORM_SNP}" "${PLATFORM_UCODE}" 3
  run_cmd snphost config set "${CONFIG_ARGS[@]}" >/dev/null || { emit_step "both_masks" "fail" "${CMD_ERROR}"; return 1; }
  run_cmd snphost show tcb >/dev/null || { emit_step "both_masks" "fail" "${CMD_ERROR}"; return 1; }
  emit_step "both_masks" "pass"
}

test_reset_after_masks() {
  run_cmd snphost config reset >/dev/null || { emit_step "reset_after_masks" "fail" "${CMD_ERROR}"; return 1; }
  local verify_output
  verify_output=$(run_cmd snphost show tcb) || { emit_step "reset_after_masks" "fail" "${CMD_ERROR}"; return 1; }
  if tcb_versions_match "${verify_output}"; then
    emit_step "reset_after_masks" "pass"
  else
    emit_step "reset_after_masks" "fail" "reported TCB should match platform TCB after mask reset"
    return 1
  fi
}

test_commit() {
  run_cmd snphost commit >/dev/null || { emit_step "commit" "fail" "${CMD_ERROR}"; return 1; }
  emit_step "commit" "pass"
}

# ─── Main ───────────────────────────────────────────────────────────────────

main() {
  if ! test_read_tcb; then
    emit_summary
    return 1
  fi

  test_config_set_lower
  test_config_reset
  test_mask_chip_id
  test_mask_chip_key
  test_both_masks
  test_reset_after_masks
  test_commit

  emit_summary
  [[ "${FAILED}" -eq 0 ]]
}

main
