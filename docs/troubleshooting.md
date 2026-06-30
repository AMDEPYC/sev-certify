# Troubleshooting

All host image services log to the systemd journal. Use `journalctl -u <service>` to inspect logs for each step.

## Host image pipeline

The host boot flow is:

```
boot.target → system.target → test.target → stop.target
```

Certification tests run in `sev-verify.service` during the test stage. Results
are written to `/root/results/` and published by `beacon-report.service` during
the stop stage.

## Service Logs by Stage

### Boot / System

```bash
journalctl -u beacon-boot.service
journalctl -u snphost-ok.service
journalctl -u systemd-journal-remote.service
```

### Test

```bash
journalctl -u sev-verify.service
```

Certification JSON and Markdown output:

```bash
ls -la /root/results/
```

### Stop

```bash
journalctl -u beacon-report.service
journalctl -u login-or-reboot.service
```

### Barrier services

These synchronization services mostly just run `/usr/bin/true`, but can be useful to confirm a stage completed:

```bash
journalctl -u system-done.service
journalctl -u test-done.service
journalctl -u stop-done.service
```

## Guest Logs

Guest logs are collected via `systemd-journal-remote` into a separate journal directory:

```bash
journalctl -D /var/log/journal/guest-logs/
```

This is useful for debugging guest-side services when `sev-verify` launches a
guest VM. Guest journal tags are not used for host certificate generation in the
current pipeline.

