# Host Configuration for logging service

`systemd-journal-remote.service` is configured,enabled and started on the host to receive the guest journal logs over the network using HTTP protocol at log location `/var/log/journal/guest-logs`.

# How to access QEMU guest logs on the host

Make sure to configure, enable and `systemd-journal-upload` service on the guest to receive the real-time guest logs over HTTP protocol at any specific journal log location(for instance, at `/var/log/journal/guest-logs` path).

Guest service logs can be accessed from the host as shown below:
```sh
$ journalctl -D /var/log/journal/guest-logs -f -u systemd-userdbd.service
```
