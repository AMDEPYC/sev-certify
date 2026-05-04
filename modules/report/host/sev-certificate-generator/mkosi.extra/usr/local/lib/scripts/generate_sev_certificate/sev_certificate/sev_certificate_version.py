import subprocess
import json
import emoji as em

test_status_emojis = {
    'pass': em.emojize(':check_mark_button:'),
    'fail': em.emojize(':cross_mark:'),
    'skip': em.emojize(':fast_forward:', language='alias'),
}


class SEV_Certificate:
    """Generic certificate generator for structured JSON test results.

    Parses step/summary JSON lines from journald, filtered by SEV_VERSION
    and grouped by SEV_TEST_GROUP. Works for any certification level that
    uses the emit_step/emit_summary JSON format.
    """

    def __init__(self, sev_version):
        self.sev_version = sev_version

    def get_test_group_summary(self):
        """Generate per-group test summaries from structured JSON results."""
        cmd = f"journalctl SEV_VERSION={self.sev_version} -o json"
        result = subprocess.run(cmd, shell=True, text=True, capture_output=True, check=True)

        groups = {}
        for line in result.stdout.strip().splitlines():
            try:
                record = json.loads(line)
                message = record.get("MESSAGE", "")
                if not message.startswith("{"):
                    continue
                entry = json.loads(message)
            except (json.JSONDecodeError, ValueError):
                continue
            group = record.get("SEV_TEST_GROUP", "unknown")
            if group not in groups:
                groups[group] = {"steps": [], "summary": None}
            if entry.get("type") == "step":
                groups[group]["steps"].append(entry)
            elif entry.get("type") == "summary":
                groups[group]["summary"] = entry

        content = ""

        for group, data in groups.items():
            summary = data["summary"]
            steps = data["steps"]

            if summary:
                overall = summary.get("status", "?")
                passed = summary.get("passed", 0)
                failed = summary.get("failed", 0)
            else:
                passed = sum(1 for s in steps if s.get("status") == "pass")
                failed = sum(1 for s in steps if s.get("status") == "fail")
                overall = "fail" if failed > 0 else "pass"

            overall_emoji = test_status_emojis.get(overall, "?")
            content += f"\n[ {overall_emoji} ] {group} ({passed} passed, {failed} failed)\n"

            for step in steps:
                emoji = test_status_emojis.get(step.get("status", "?"), "?")
                name = step.get("test", "?")
                detail = step.get("detail", "")
                line = f"\t{emoji} {name}"
                if detail:
                    line += f"  ({detail})"
                content += line + "\n"

        return content.expandtabs(2)

    def get_sev_log(self):
        """Get raw journal log for this certification level."""
        cmd = f"journalctl SEV_VERSION={self.sev_version} --no-hostname --utc"
        result = subprocess.run(cmd, shell=True, text=True, capture_output=True, check=True)
        return result.stdout

    def generate_sev_certificate(self):
        """Generate the SEV Certificate content for this level."""
        content = "\n ====== SEV CERTIFICATE ====== \n"
        content += f"\n SEV VERSION: {self.sev_version} \n"

        content += "\n=== SUMMARY ===\n"
        content += self.get_test_group_summary()

        content += f"\n=== SEV VERSION {self.sev_version} LOG ===\n"
        content += self.get_sev_log()

        return content.expandtabs(2)
