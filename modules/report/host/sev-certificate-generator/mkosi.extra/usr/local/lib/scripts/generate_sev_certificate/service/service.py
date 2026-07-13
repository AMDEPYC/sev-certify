import sys
import subprocess
import re

class Service:
    """ Parse any useful service information from the systemd service message """

    guest_logs_path = "/var/log/journal/guest-logs/"

    def get_service_message(self, service, platform):
        match platform:
            case "host" :
                host_service_message = f"journalctl  -u {service} -o cat"
                service_message_command = host_service_message

            case "guest" :
                guest_service_message = f"journalctl -D {self.guest_logs_path} -u {service} -o cat"
                service_message_command = guest_service_message

        command = subprocess.run(service_message_command, shell=True, check=True, text=True, capture_output=True)
        service_message = command.stdout

        return service_message

    def get_service_description(self, service, platform):
        """Get the SNP host/guest test service description."""
        description_line_keyterm = "starting"
        match platform:
            case "host" :
                host_service_description = f"journalctl -o cat | grep -i {description_line_keyterm} | grep -i {service} | head -1"
                service_description_command = host_service_description

            case "guest" :
                guest_service_description = f"journalctl -D {self.guest_logs_path} -o cat | grep -i {description_line_keyterm} | grep -i {service} | head -1"
                service_description_command = guest_service_description

        command = subprocess.run(service_description_command, shell=True, check=True, text=True, capture_output=True)

        # Receive "<service name>-<service description>" text from the command output
        service_detail = command.stdout

        # Parse the <service description> part
        match = re.split(r'(?i)-\s+', service_detail, maxsplit=1)
        service_description=match[1].strip()
        return service_description

    def extract_service_status(self, service, platform):
        """
        Parse service status from the service message using priority: failed > skipped > passed.
        Return ? if none matched.
        """
        service_message = self.get_service_message(service, platform)

        # Priority-checked patterns: (status, pattern)
        PATTERNS = [
            ("failed", re.compile(rf'Failed to start {service} ', re.IGNORECASE)),
            ("skipped", re.compile(rf'was skipped', re.IGNORECASE)),
            ("passed", re.compile(rf'{service}: Deactivated successfully', re.IGNORECASE)),
        ]

        # Evaluate in PATTERNS order which already has desired priority
        for status, pattern in PATTERNS:
            if pattern.search(service_message):
                return status
        return "?"

    def extract_service_error(self, failed_service, platform="guest"):
        """
        Parse for the service error message for a failed service status.
        """
        # Extracts error details between the service_start_line and service_end_line, excluding these lines.
        match platform:
            case "host":
              failed_service_cmd = f"journalctl -u {failed_service}"
            case "guest":
                failed_service_cmd = f"journalctl  -D {self.guest_logs_path} -u {failed_service}"

        service_error_cmd = f" {failed_service_cmd}|  awk 'BEGIN{{IGNORECASE=1}} !/systemd/\'"

        command = subprocess.run(service_error_cmd, shell=True, check=True, text=True, capture_output=True)
        service_error = command.stdout.strip()

        service_error_lines = service_error.splitlines()
        clean_lines = [ line.split("]:")[-1] for line in service_error_lines ]
        service_error = "\n".join(clean_lines)

        return service_error
