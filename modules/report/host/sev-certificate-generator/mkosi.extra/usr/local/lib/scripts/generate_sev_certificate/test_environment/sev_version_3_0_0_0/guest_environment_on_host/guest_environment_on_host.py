import sys
import subprocess
import textwrap

class GuestEnvironment:
    """ Show guest environment details """

    guest_logs_path = "/var/log/journal/guest-logs/"
    guest_environment_metadata = "GUEST_ENVIRONMENT=3.0.0-0"

    def show_guest_environment_on_host(self):

        guest_environment = "\n Guest Environment Details:"

        ge_on_host_command = f"journalctl -D {self.guest_logs_path} {self.guest_environment_metadata} -o cat | grep -v .service"
        command = subprocess.run(ge_on_host_command, shell=True, check=False, text=True, capture_output=True)

        # Handle the error when the command for displaying guest environment fails
        command_error = command.stderr.strip()
        error_message = ''

        if command.returncode !=0:
            error_message += f"{guest_environment}" + "\n"
            error_message += "\t" + f"Display of guest environment detail fails with the exit code {command.returncode}" + "\n"
            error_message += "\t" + f"{command_error}"
            error_message = error_message.expandtabs(2)
            return error_message

        guest_environment_result = command.stdout.strip()
        guest_environment_result = textwrap.indent(guest_environment_result, "\t")

        guest_environment += "\n" + guest_environment_result
        return guest_environment
