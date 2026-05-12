import os
import emoji as em

from sev_certificate_version_3_0_0_0 import SEV_Certificate as SEV_Certificate_v3_0_0_0
from sev_certificate_version import SEV_Certificate as SEV_Certificate_structured

FAIL_MARKER = em.emojize(':cross_mark:')

# Certification levels in ascending order.
# v3.0.0-0 uses its own class (service-based status extraction).
# All subsequent levels use the structured JSON class, just with different version strings.
levels = [
    SEV_Certificate_v3_0_0_0(),
    SEV_Certificate_structured("3.0.0-1"),
]

combined = ''
highest_passed = None

for cert in levels:
    content = cert.generate_sev_certificate()
    combined += content
    if FAIL_MARKER not in content:
        highest_passed = cert.sev_version

print(combined)

# Write one combined cert named after highest achieved level
if highest_passed:
    output_file = os.path.expanduser(f"~/sev_certificate_v{highest_passed}.txt")
else:
    output_file = os.path.expanduser("~/sev_certificate.txt")

with open(output_file, "w") as f:
    f.write(combined)

print(f"Certificate saved to: {output_file}")
if highest_passed:
    print(f"Highest achieved level: {highest_passed}")
else:
    print("No certification level achieved")
