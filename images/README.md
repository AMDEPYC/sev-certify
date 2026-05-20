# Images

mkosi image definitions for SEV-SNP host and guest environments. Each subdirectory contains a `mkosi.conf` for one image.

## Build

```bash
make list                    # show available images
make host-fedora-41          # build a specific image
make all                     # build every image
make clean-host-fedora-41    # clean a specific image
make clean                   # clean every image
make status                  # show built images and their artifacts
```

## Naming

```
{role}-{distro}-{release}
```

- **role**: `host` (bare-metal SEV hypervisor) or `guest` (SEV guest UKI/EFI)
- **distro**: `fedora`, `ubuntu`, `centos`, `debian`, `rocky`
- **release**: distro version number or codename

## Ubuntu: AppArmor + mkosi

Ubuntu restricts unprivileged user namespaces via AppArmor, which breaks mkosi. See [systemd/mkosi#3265](https://github.com/systemd/mkosi/issues/3265).

**Quick fix** -- disable the restriction system-wide:

> **Warning:** This disables AppArmor's unprivileged user namespace restrictions for *all* processes, not just mkosi. On multi-user or production systems, prefer the per-binary fix below.

```bash
sudo sysctl -w kernel.apparmor_restrict_unprivileged_unconfined=0
sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0
```

**Per-binary fix:** Create `/etc/apparmor.d/mkosi` (adjust path if needed):

```
abi <abi/4.0>,
include <tunables/global>

profile mkosi /usr/bin/mkosi flags=(unconfined) {
  userns,
  include if exists <local/mkosi>
}
```

Then reload:

```bash
sudo systemctl reload apparmor
```
