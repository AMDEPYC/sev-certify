# Logging UKI

Guest journal messages from the QEMU guest can be forwarded over local network to the host through `systemd journal upload/remote` service using NAT networking via HTTP protocol with the port forwarding approach.

# How to run the SNP enabled QEMU Guest

1. Download and unzip the artifacts for your relevant guest distro.

2. Run SNP enabled QEMU Guest enabed with the guest port forwarded to the localhost on the default `systemd-journal-remote.service` port 19532:

```sh
$ qemu-system-x86_64 \
    -enable-kvm \
    -cpu EPYC-v4 \
    -smp 1 \
    -netdev user,id=net0,guestfwd=tcp:10.0.2.100:19532-tcp:127.0.0.1:19532 \
    -device virtio-net-pci,netdev=net0 \
    -machine memory-encryption=sev0,vmport=off \
    -object memory-backend-memfd,id=ram1,size=2048M \
    -machine memory-backend=ram1 \
    -object sev-snp-guest,id=sev0,cbitpos=51,reduced-phys-bits=1,kernel-hashes=on \
    -bios /usr/share/edk2/ovmf/OVMF.amdsev.fd \
    -kernel images/guest-fedora-41/image.efi \
    -nographic
```

# How to access QEMU guest logs on the host

Make sure to configure, enable and `systemd-journal-remote` service on the host to receive the real-time guest logs over HTTP protocol.

Guest service logs can be accessed from the host as shown below:
```sh
$ journalctl -D /var/log/journal/guest-logs -f -u systemd-userdbd.service
```

