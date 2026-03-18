#!/bin/bash

# Environment Variables
ATTESTATION_DIR="/usr/local/lib/attestation_service"
ATTESTATION_STATUS_LOG="/usr/local/lib/attestation_status"

# Utility function to check the error status of each step in the attestation workflow
check_command_status() {
  local command_status=$1
  local command_name=$2
  local command_output=$3

  # Store the snpguest command status in a json file
  jq -nc \
     --arg command_status_value "${command_status}" \
     --arg command_name_key "${command_name}" \
    '{ ($command_name_key): $command_status_value }' >> ${ATTESTATION_STATUS_LOG}

  # Print command status
  if [[ $command_status -ne 0 ]]; then
    >&2 echo -e "ERROR: ${command_name} fails !! \n${command_output}"
    return 1
  else
    echo -e "${command_output}\n"
  fi
}

snpguest_regular_attestation_workflow() {
  # Create a fresh attestation working directory
  [  -d "${ATTESTATION_DIR}" ] &&  rm -rf "${ATTESTATION_DIR}"
  mkdir -p "${ATTESTATION_DIR}"

  # Generate the SNP Attestation Report using a randomly generated request data
  { snp_guest_report=$(snpguest report ${ATTESTATION_DIR}/attestation-report.bin ${ATTESTATION_DIR}/random-request-data.bin --random 2>&1); report_status=$?; }
  check_command_status "${report_status}" "Generation of SNP Guest Report" "${snp_guest_report}" || return 1

  # Fetch the ARK, ASK certificate chain from Key Distribution Server
  { fetch_ca=$(snpguest fetch ca pem -r "${ATTESTATION_DIR}/attestation-report.bin" "${ATTESTATION_DIR}/certificates" 2>&1); fetch_ca_status=$?; }
  check_command_status "${fetch_ca_status}" "Fetch of CA certificate chain" "${fetch_ca}" || return 1

  # Fetch the VCEK certificate chain from Key Distribution Server
  { fetch_vcek=$(snpguest fetch vcek pem ${ATTESTATION_DIR}/certificates/ ${ATTESTATION_DIR}/attestation-report.bin 2>&1); fetch_vcek_status=$?; }
  check_command_status "${fetch_vcek_status}" "Fetch of VCEK certificate chain" "${fetch_vcek}" || return 1

  # Verify if the ARK, ASK and VCEK certificate chain are signed properly
  { verify_cert_chain=$(snpguest verify certs ${ATTESTATION_DIR}/certificates/ 2>&1); verify_cert_chain_status=$?; }
  check_command_status "${verify_cert_chain_status}" "Verification of ARK, ASK and VCEK cert-chain" "${verify_cert_chain}" || return 1

  # Verify the SNP Attestation Report
  { verify_attestation=$(snpguest verify attestation ${ATTESTATION_DIR}/certificates/ ${ATTESTATION_DIR}/attestation-report.bin 2>&1); verify_attestation_status=$?; }
  check_command_status "${verify_attestation_status}" "Verification of SNP Attestation Report" "${verify_attestation}" || return 1

  # Show the SNP Attestation Report for the randomly generated request data
  { show_attestation_report=$(snpguest display report ${ATTESTATION_DIR}/attestation-report.bin 2>&1); show_attestation_report_status=$?; }
  check_command_status "${show_attestation_report_status}" "Display of SNP Attestation Report" "${show_attestation_report}" || return 1
  echo -e "\nSNP Attestation Report generated successfully !!\n${show_attestation_report}"

}

validate_request_data(){
  # local random_request_data="$(tr -d '\n'< "${ATTESTATION_DIR}/random-request-data.txt")"
  local random_request_data snpguest_report_request_data
  random_request_data="$(xxd -p -c 0 "${ATTESTATION_DIR}/random-request-data.bin" | tr '[:upper:]' '[:lower:]')"

  echo -e "\nRandom Request Data:"
  echo -e "${random_request_data}"

  # Get the measurement attribute from the SNP Attestation Report
  snpguest_report_request_data=$(snpguest display report ${ATTESTATION_DIR}/attestation-report.bin \
											| tr '\n' ' ' | sed "s|.*Report Data:\(.*\)Measurement.*|\1\n|g" \
											| sed "s| ||g" \
											| tr '[:upper:]' '[:lower:]'
										)
  
  snpguest_report_request_data=$(echo ${snpguest_report_request_data} | sed $'s/[^[:print:]\t]//g')

  echo -e "\nRequest Data from SNP Attestation Report:"
  echo -e "${snpguest_report_request_data} \n"

  # Compare the expected request data to the guest report request data
  [[ "${random_request_data}" == "${snpguest_report_request_data}" ]] \
    && echo -e "The random request data generated matches the snp guest report request data!"    \
    || { >&2 echo -e "FAIL: Request data do not match"   ; return 1; }
}

validate_snp_measurement(){
  # Get the expected measurement from Host Data Attribute in SNP Attestation Report
  local expected_measurement=$(snpguest display report ${ATTESTATION_DIR}/attestation-report.bin \
											| tr '\n' ' ' | sed "s|.*Host Data:\(.*\)ID Key Digest:.*|\1\n|g" \
											| sed "s| ||g" \
											| tr '[:upper:]' '[:lower:]'
										)
  expected_measurement=$(echo ${expected_measurement} | sed $'s/[^[:print:]\t]//g')

  echo -e "\nExpected Measurement:"
  echo -e "${expected_measurement}"

  # Get the SHA-256 sum of the measurement attribute from the SNP Attestation Report
  local snpguest_report_measurement=$(snpguest display report ${ATTESTATION_DIR}/attestation-report.bin \
											| tr '\n' ' ' | sed "s|.*Measurement:\(.*\)Host Data.*|\1\n|g" \
											| sed "s| ||g" \
											| tr '[:upper:]' '[:lower:]'
										)
  #snpguest_report_measurement=$(echo ${snpguest_report_measurement} | sed $'s/[^[:print:]\t]//g')
  snpguest_report_measurement=$(echo ${snpguest_report_measurement} | sha256sum | cut -d ' ' -f 1 )

  echo -e "\nMeasurement from SNP Attestation Report:"
  echo -e "${snpguest_report_measurement} \n"

  # Compare the expected measurement with the guest report measurement
  [[ "${expected_measurement}" == "${snpguest_report_measurement}" ]] \
	&& echo -e "\nThe expected measurement matches the snp guest report measurement!" \
	|| { >&2 echo -e "\nFAIL: measurements do not match"; return 1; }
}

main() {
  echo -e "\nPerform Regular Attestation workflow using snpguest tool ..."
  snpguest_regular_attestation_workflow || return 1

  echo -e "\nValidate Request Data Attribute ..."
  validate_request_data || return 1

  echo -e "\nValidate Measurement Attribute ..."
  validate_snp_measurement || return 1
}

main