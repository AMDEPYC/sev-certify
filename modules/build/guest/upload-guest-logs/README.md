# Guest Configuration for logging Service

`systemd-journal-remote.service` is enabled and started on the guest to forward the guest journal logs into the host over the network using HTTP protocol.

# How to access QEMU guest logs on the host

Make sure to configure, enable and `systemd-journal-remote` service on the host to receive the real-time guest logs over HTTP protocol at any specific journal log location(for instance, at `/var/log/journal/guest-logs` path).

Guest service logs can be accessed from the host as shown below:
```sh
$ journalctl -D /var/log/journal/guest-logs -f -u systemd-userdbd.service
```
