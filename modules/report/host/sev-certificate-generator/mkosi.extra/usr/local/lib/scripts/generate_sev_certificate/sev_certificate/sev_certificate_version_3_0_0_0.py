import sys
import subprocess
import os
import emoji as em
import textwrap
import re
import json

# Import user-defined modules located at sibling directory in the parent folder
from pathlib import Path
current_dir = Path(__file__).resolve().parent

# Import show_test_environment module of sev_version 3.0.0-0
test_environment_directory = current_dir.parent / 'test_environment'/ 'sev_version_3_0_0_0'
test_environment_directory = str(test_environment_directory)
sys.path.append(test_environment_directory)
from test_environment import TestEnvironment

# Import Service module
service_directory = current_dir.parent / 'service'
service_directory = str(service_directory)
sys.path.append(service_directory)
from service import Service

# Constants
test_status_emojis = {
    'passed': em.emojize(':check_mark_button:'),
    'failed': em.emojize(':cross_mark:'),
    'skipped': em.emojize(':fast_forward:', language='alias')
}

# Generates SEV Report of version 3.0.0-0
class SEV_Certificate:
    """ Generates SEV Certificate of version 3.0.0-0 """

    sev_metadata_key = "SEV_VERSION="
    sev_version = "3.0.0-0"
    guest_logs_path = "/var/log/journal/guest-logs/"
    sev_service = Service()
    test_environment = TestEnvironment()

    def get_snp_host_summary(self):
        """Generate all SNP Host tests summary."""

        # Get the list of all SNP host test services in the sequential order.
        snphost_test = f"journalctl SNPHOST_TEST={self.sev_version} -o json | jq -r '._SYSTEMD_UNIT' | grep -i service | awk 'NF && !seen[$0]++'"
        command = subprocess.run(snphost_test, shell=True, check=True, text=True, capture_output=True)
        snphost_services = command.stdout
        snphost_services_list = snphost_services.splitlines()

        # Map SNP Host test services with their statuses.
        snphost_services_status ={}

        for service in snphost_services_list:
            service_status = self.sev_service.extract_service_status(service,"host")
            snphost_services_status[service] = service_status

        # Create the SNP host test summary.
        content = ''
        snphost_emoji = '?'

        for service, service_status in snphost_services_status.items():
            emoji = test_status_emojis.get(service_status.lower(),'?')
            content += "\t" + f"{emoji} {service} :"
            service_description = self.sev_service.get_service_description(service, "host")
            content += "  " + service_description + "\n"

            # Set overall SNP host test status emoji based on the single failed/skipped SNP test
            if service_status.lower() == 'failed':
                snphost_emoji = 'failed'
                content += f"\t\t{service} fails !!! Please check below for more details:"
                content += "\n" + textwrap.indent(self.sev_service.extract_service_error(service, "host"),"\t\t") + "\n"
            elif service_status.lower() == 'skipped':
                snphost_emoji = 'skipped'
            else:
                snphost_emoji = 'passed'

        snphost_emoji = test_status_emojis.get(snphost_emoji, '?')
        snphost_status = "\n[ " + snphost_emoji + " ] " + f"SEV VERSION {self.sev_version} SNP HOST TESTS" + "\n"

        snphost_summary = snphost_status + content
        snphost_summary = snphost_summary.expandtabs(2)
        return snphost_summary

    def get_snp_guest_attestation_summary(self):
        """Generate SNP Guest Attestation summary from the SNP guest attestation status service."""

        # Get journal entries with SNP Guest Attestation test results
        guest_attestation_service="attestation-result.service"
        snpguest_attestation_cmd = f"journalctl -D {self.guest_logs_path} -u {guest_attestation_service} -o cat"
        snpguest_attestation_result = subprocess.run(snpguest_attestation_cmd, shell=True, check=True, text=True, capture_output=True)

        # Extract and parse JSON objects the "snpguest_attestation_result" string
        json_objects = re.findall(r'\{[^}]+\}', snpguest_attestation_result.stdout)

        snpguest_attestation_data = {}
        for obj in json_objects:
            snpguest_attestation_data.update(json.loads(obj))

        # Convert the status codes into the human-readable form
        for snpguest_step, status_code in snpguest_attestation_data.items():
            snpguest_attestation_data[snpguest_step] = "passed" if int(status_code) == 0 else "failed"

        # Format the output with test emojis
        snpguest_attestation_summary = ''
        for step, step_status in snpguest_attestation_data.items():
            emoji = test_status_emojis.get(step_status.lower(), '?')
            snpguest_attestation_summary += "\t\t\t " + f"{emoji} {step}" + "\n"

        return snpguest_attestation_summary

    def get_snp_guest_summary(self):
        """Generate all SNP Guest tests summary."""

        # Get the list of all SNP Guest test services
        snpguest_command = f"journalctl -D {self.guest_logs_path} SNPGUEST_TEST={self.sev_version} -o json | jq -r '._SYSTEMD_UNIT' | grep -i service | awk 'NF && !seen[$0]++'"
        command = subprocess.run(snpguest_command, shell=True, check=True, text=True, capture_output=True)
        snpguest_services = command.stdout
        snpguest_services_list = snpguest_services.splitlines()

        # Map SNP Guest test service name with its status
        snpguest_services_status ={}

        for service in snpguest_services_list:
            service_status = self.sev_service.extract_service_status(service, "guest")
            snpguest_services_status[service] = service_status

        # Create SNP Guest test summary
        content = ''
        snpguest_emoji = ''

        guest_attestation_summary = self.get_snp_guest_attestation_summary() + "\n"

        for service, service_status in snpguest_services_status.items():
            emoji = test_status_emojis.get(service_status.lower(),'?')
            content += "\t" + f"{emoji} {service} :"
            service_description = self.sev_service.get_service_description(service, "guest")
            content += "  " + service_description + "\n"

            # Add step-by-step summary status of the guest attestation workflow
            if "attestation-workflow.service" in service.lower() :
                content += guest_attestation_summary

            # Set "snpguest_emoji" status based on the single failed/skipped SNP test
            if service_status.lower() == 'failed':
                snpguest_emoji = 'failed'
                content += f"\t\t{service} fails !!! Please check below for more details:"
                content += "\n" + textwrap.indent(self.sev_service.extract_service_error(service,"guest"),"\t\t") + "\n"
            elif service_status.lower() == 'skipped':
                snpguest_emoji = 'skipped'
            else:
                snpguest_emoji = 'passed'

        snpguest_emoji = test_status_emojis.get(snpguest_emoji, '?')
        snpguest_status = "\n[ " + snpguest_emoji + " ] " + f"SEV VERSION {self.sev_version} SNP GUEST TESTS \n"
        snpguest_summary = snpguest_status + content
        snpguest_summary = snpguest_summary.expandtabs(2)
        return snpguest_summary

    def get_sev_log(self):
        """Get the SEV log details using the SEV_VERSION log metadata attribute."""

        sev_metadata = self.sev_metadata_key + self.sev_version
        sev_metadata = sev_metadata.strip()
        service_command = f"journalctl {sev_metadata}  --no-hostname --utc"
        command = subprocess.run(service_command, shell=True, check=True, text=True, capture_output=True)

        sev_log = command.stdout
        return sev_log

    def write_sev_certificate(self, certificate_content,  output_file="~/sev_certificate.txt"):
        """Save the generated SEV Certificate to a text file."""

        # Expand ~ to the home directory
        output_file = os.path.expanduser(output_file)

        # Ensure the parent directory exists
        os.makedirs(os.path.dirname(output_file), exist_ok=True)

        with open(output_file, "w") as f:
            f.write(certificate_content)

        print(f"SEV version {self.sev_version} Certificate saved to: {output_file}")

    def generate_sev_certificate(self):
        """ Generate the SEV Certificate content """

        sev_certificate_content ='\n ====== SEV CERTIFICATE ====== \n'
        sev_certificate_content += f'\n SEV VERSION: {self.sev_version} \n'

        sev_certificate_content += f"\n === TEST ENVIRONMENT DETAILS === \n"
        sev_certificate_content += self.test_environment.show_test_environment()

        sev_certificate_content += "\n=== SUMMARY ===\n"
        sev_certificate_content += self.get_snp_host_summary()
        sev_certificate_content += self.get_snp_guest_summary()

        sev_certificate_content += f"\n=== SEV VERSION {self.sev_version} LOG ===\n"
        sev_certificate_content += self.get_sev_log()

        sev_certificate_content = sev_certificate_content.expandtabs(2)
        return sev_certificate_content

