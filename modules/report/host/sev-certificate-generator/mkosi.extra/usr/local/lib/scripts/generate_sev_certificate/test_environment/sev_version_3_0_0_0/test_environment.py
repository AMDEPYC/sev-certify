import sys

# Import user-defined modules located in different directory path
from pathlib import Path
current_dir = Path(__file__).resolve().parent

# Import host environment module
he_directory = current_dir / 'host_environment'
he_directory = str(he_directory)
sys.path.append(he_directory)
from host_environment import HostEnvironment

# Import guest environment module
ge_directory = current_dir / 'guest_environment_on_host'
ge_directory = str(ge_directory)
sys.path.append(ge_directory)
from guest_environment_on_host import GuestEnvironment

class TestEnvironment:

    he = HostEnvironment()
    ge = GuestEnvironment()

    def show_test_environment(self):
        """ Display the host and SNP guest environment details """

        test_environment = self.he.show_host_environment() + "\n"
        test_environment += self.ge.show_guest_environment_on_host() + "\n"

        return test_environment

