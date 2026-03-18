import sys
import subprocess
import os
import emoji as em
import textwrap
import re
import json

# Import user-defined modules located at sibling directory in the parent folder
from sev_certificate_version_3_0_0_0 import SEV_Certificate as sev_certificate_v_3_0_0_0

sev_report = ''

# Get SEV Certificate Version 3.0.0-0
sev_report_v_3_0_0_0 = sev_certificate_v_3_0_0_0()
sev_report += sev_report_v_3_0_0_0.generate_sev_certificate()

# Print SEV Certificate into the console
print(sev_report)

# Write certificate to file
sev_report_v_3_0_0_0.write_sev_certificate(sev_report, "~/sev_certificate_v3.0.0-0.txt")

