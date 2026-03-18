import sys
import subprocess
import re

# Import user-defined modules located at different location
from pathlib import Path
current_dir = Path(__file__).resolve().parent

# Import host_os_package module
current_dir = str(current_dir)
sys.path.append(current_dir)
from host_os_package import HostOSPackage
import textwrap

class HostEnvironment:
    """ Show host environment details """

    package_version_path=current_dir + "/"+ "package_version.sh"

    # Get Host OS information
    def get_host_os_version(self):
        os_version_command = f"hostnamectl | grep \"Operating System\" | cut -d':' -f2"
        # os_version_command = f"cat /etc/os-release | grep PRETTY_NAME"
        command = subprocess.run(os_version_command, shell=True, check=True, text=True, capture_output=True)
        os_version = command.stdout.strip()

        # os_version = os_version.replace("PRETTY_NAME=", "").replace("\"", "").strip()
        host_os_version = "Host Operating System: " + os_version
        return host_os_version

    def get_host_os_id(self):
        os_version_command = f"grep \'^ID=\' /etc/os-release | cut -d\'=\' -f2"
        os_version_command = os_version_command.strip()
        command = subprocess.run(os_version_command, shell=True, check=True, text=True, capture_output=True)
        host_os_id = command.stdout.strip()
        host_os_id = host_os_id.replace('"','')
        return host_os_id

    # Get installed host package versions
    def get_ovmf_version(self):
        os_id = self.get_host_os_id()
        ovmf_pkg_name = HostOSPackage.ovmf.get(os_id)
        ovmf_command = subprocess.run([self.package_version_path, ovmf_pkg_name], capture_output=True, text=True, check=True)
        ovmf_version = ovmf_command.stdout.strip()
        return "OVMF Version: " + ovmf_version

    def get_qemu_version(self):
        os_id = self.get_host_os_id()
        qemu_pkg_name = HostOSPackage.qemu.get(os_id)
        qemu_command = subprocess.run([self.package_version_path, qemu_pkg_name], capture_output=True, text=True, check=True)
        qemu_version = qemu_command.stdout.strip()
        return "QEMU Version: " + qemu_version

    def get_host_kernel_version(self):
        host_kernel_version_command = "uname -r"
        kernel_command = subprocess.run(host_kernel_version_command, shell=True, capture_output=True, text=True, check=True)
        host_kernel_version = kernel_command.stdout.strip()
        return "Host Kernel Version: " + host_kernel_version

    # Show All host environment details
    def show_host_environment(self):

        host_environment = "\n Host Environment Details: \n"

        host_environment_result = self.get_host_os_version() + "\n"
        host_environment_result += self.get_ovmf_version() + "\n"
        host_environment_result += self.get_qemu_version() + "\n"
        host_environment_result += self.get_host_kernel_version()

        host_environment += textwrap.indent(host_environment_result, "\t")

        return host_environment
