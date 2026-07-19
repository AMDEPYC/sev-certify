# Images

mkosi image definitions for SEV-SNP host and guest environments. Each subdirectory contains a `mkosi.conf` for one image.

## Build

### 1. Fetch dependency tags

```bash
make tags
```

This queries the GitHub API for the latest release tag of each build dependency and writes them to `.tags`:

| Variable | Repository | Used by |
|---|---|---|
| `SNPGUEST_TAG` | [virtee/snpguest](https://github.com/virtee/snpguest) | Guest and host images |
| `BEACON_TAG` | [AMDEPYC/beacon](https://github.com/AMDEPYC/beacon) | Host images |
| `SNPHOST_TAG` | [virtee/snphost](https://github.com/virtee/snphost) | Host images |

The `.tags` file is passed to mkosi via `--env-file` so each build script uses the pre-fetched tag rather than calling the GitHub API individually. This avoids rate-limit issues when building multiple images (the GitHub API allows 60 unauthenticated requests/hour).

Re-run `make tags` whenever you want to pick up a new release of one of the dependencies. The `.tags` file is not checked in, so it won't update automatically.

If you hit rate limits, set a token first:

```bash
export GITHUB_TOKEN=$(gh auth token)
make tags
```

> **Note**: In CI, the workflow fetches these same tags and injects them directly into mkosi configs, so `make tags` is only needed for local builds.

### 2. Build images

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
