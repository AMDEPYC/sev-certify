#!/bin/bash

set -euo pipefail

get_guest_os_version() {
  local os_version="$(hostnamectl | grep "Operating System" | cut -d ':' -f2)"
  echo -e "Guest Operating System: ${os_version}"
}

get_guest_kernel_version() {
  local guest_kernel_version="$(uname -r)"
  echo -e "Guest Kernel version: ${guest_kernel_version}"
}

main() {
  get_guest_os_version
  get_guest_kernel_version
}

main