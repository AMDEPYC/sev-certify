#!/usr/bin/env bash
# Script to fetch the package version for installed packages on the different OS distributions
# Uses a 'case' (match) statement where each case corresponds to a package manager
# Usage: ./package_version.sh PACKAGE

set -euo pipefail

if [ $# -ne 1 ]; then
  echo "Usage: $0 PACKAGE"
  exit 2
fi

package="$1"

declare -A package_managers
package_managers=( ["fedora"]="rpm" ["ubuntu"]="apt" ["debian"]="apt" ["centos"]="rpm" ["rocky"]="rpm" )

os_name=$(grep '^ID=' /etc/os-release | cut -d'=' -f2 | tr -d '"')
os_package_manager=${package_managers[${os_name}]}

case "$os_package_manager" in
  apt)
    package_version=$(apt-cache policy ${package} | awk '/Installed:/ {print $2}')
    echo -e "${package_version}"
    ;;
  rpm)
    package_version=$(rpm -q --queryformat '%{VERSION}-%{RELEASE}' ${package} 2>/dev/null || echo "" )
    echo -e "${package_version}"
    ;;
  *)
    echo "No supported package manager detected (apt/rpm)."
    exit 3
    ;;
esac
