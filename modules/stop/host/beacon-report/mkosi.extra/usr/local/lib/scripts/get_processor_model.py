"""Print the host EPYC codename and series for beacon labels.

Identification lives in sev_verify.processor. This script is the image-facing
entry point beacon-report.sh already calls.
"""

import argparse

from sev_verify.processor import CpuidReadError, identify_host_processor


def get_processor_model():
    """Return ``(codename, series)``, or the string ``invalid cpu``."""
    try:
        processor = identify_host_processor()
    except CpuidReadError:
        return "invalid cpu"
    if processor is None:
        return "invalid cpu"
    return processor.name, processor.series


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Get AMD processor model information")
    parser.add_argument(
        "field",
        nargs="?",
        choices=["codename", "series"],
        help='Field to return: codename (e.g., "Naples") or series (e.g., "7001")',
    )
    args = parser.parse_args()

    result = get_processor_model()

    if result == "invalid cpu":
        print(result)
    elif args.field == "codename":
        print(result[0])
    elif args.field == "series":
        print(result[1])
    else:
        print(f"{result[0]} {result[1]}")
