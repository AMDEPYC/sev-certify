# Images

mkosi image definitions for SEV-SNP host and guest environments. Each subdirectory contains a `mkosi.conf` for one image.

## Build

### 1. Dependency versions

Image builds read the release tag of each build dependency from [`versions.env`](../versions.env), so they are not necessarily the latest upstream release. If an entry is left empty, the latest upstream release is looked up on GitHub instead:

| Variable | Repository | Used by |
|---|---|---|
| `SNPGUEST_TAG` | [virtee/snpguest](https://github.com/virtee/snpguest) | Guest and host images |
| `BEACON_TAG` | [AMDEPYC/beacon](https://github.com/AMDEPYC/beacon) | Host images |
| `SNPHOST_TAG` | [virtee/snphost](https://github.com/virtee/snphost) | Host images |

#### Excluded tags (not passed to mkosi)

You can track version details for components not involved in the mkosi build process by logging them in `versions.env` as excluded tags:

| Variable | Repository | Purpose |
|---|---|---|
| `DISPATCH_TAG` | [AMDEPYC/dispatch](https://github.com/AMDEPYC/dispatch) | Release to run on Dispatch Host with these images; not built into images |

Excluded tags like `DISPATCH_TAG` are allowed in `versions.env` but not passed to mkosi. The `check_versions_env` script validates that the TAG name is listed in `versions.env`, but several additional checks are skipped for excluded tags: release existence on GitHub, asset presence, and download availability are not verified.

The resolved tags are written to `.tags` and passed to mkosi via `--env-file`, so each build script uses them rather than calling the GitHub API individually. A latest tag is looked up at most once per `make` run, by following the `github.com/<repo>/releases/latest` redirect, so no GitHub token is needed and the API rate limit (60 unauthenticated requests/hour) does not apply.

To check `versions.env` and see the tags a build will use:

```bash
make versions
```

```
SNPGUEST_TAG=v0.10.0
BEACON_TAG=v1.1.0
SNPHOST_TAG=v0.9.0
```

This validates `versions.env`, confirms every pinned tag is an existing GitHub release and that the files the builds download are available, then writes the tags to `.tags` and prints them. Any problem stops `make` before mkosi starts. Every image build runs it first, so run it alone only to preview tags or check a `versions.env` change. `make check-versions` runs just the checks.

To try a different version without editing `versions.env`, override it on the command line (an empty value fetches the latest release). The override applies only to that command:

```bash
make SNPGUEST_TAG=v0.10.0 guest-fedora-41
make SNPGUEST_TAG= guest-fedora-41
```

> **Note**: In CI, the `resolve-tags` job of the `build-and-release` workflow resolves these same tags from `versions.env` and the build jobs inject them directly into mkosi configs.

### 2. Build images

```bash
make list                    # show available images
make host-fedora-41          # build a specific image
make all                     # build every image
make clean-host-fedora-41    # clean a specific image
make clean                   # clean every image
make status                  # show built images and their artifacts
```

### Manifest generation (optional)

By default, manifest and changelog files are **not** generated to reduce build time (~100 seconds). To enable them, set `GENERATE_MANIFEST` to `y`, `yes`, or `1` when running make:

```bash
make GENERATE_MANIFEST=yes all
```

The generated files (`.manifest`, `.changelog`, `.json`) provide a Software Bill of Materials (SBOM) for each image.

To disable manifest generation, omit the variable (it defaults to empty).

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
