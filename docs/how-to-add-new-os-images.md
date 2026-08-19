# Create, test and publish new guest/host OS images

You are advised to refer to the linux distribution files in [mkosi upstream distribution](https://repology.org/project/mkosi/versions) to determine OS support for configuring and building new OS support within the `sev-certify` project. If the OS support is unavailable, you can connect with mkosi community via forums, mailing lists, or the [mkosi GitHub issue tracker](https://github.com/systemd/mkosi/issues).

You can create and publish new host or guest images in `sev-certify` with `mkosi` tool in the following steps:

- **<ins>Fork sev-certify:</ins>** Begin by creating your own fork of the [sev-certify](https://github.com/AMDEPYC/sev-certify.git) project.

- **<ins>Develop new mkosi Templates for Host and Guest OS Images:</ins>**

  Within your forked repository, you should:
    - Create new OS directories named `host-<new-os-name>-<new-os-release>` and `guest-<new-os-name>-<new-os-release>` in the `sev-certify/images` directory.
    - Find an existing OS template in the `sev-certify` project that resembles your new OS. For example, to create a CentOS linux image, you can refer to the Fedora linux mkosi template.
    - Start configuring your new mkosi template by copying the identified similar linux templates into your new OS directories.

    If your new mkosi template differs from all existing templates, ensure to meet the minimum linux package requirements as detailed in the configuration [section](#configure-new-mkosi-template-for-host-and-guest-os-images).

- **<ins>Integrate new OS Release into GitHub Workflow:</ins>**

  Add new host/guest OS images to your forked `sev-certify` GitHub repository by including the new OS release in the `distro matrix` of the `build-and-release.yml` workflow located in `sev-certify/.github/workflows`:
  ```
      - distro: <new-os-name>
        release: <new-os-release>
  ```

- **<ins>Build new Host and Guest OS images:</ins>**

  You can choose between two methods to build new host and guest OS images:

  - <i>**Option 1: Build Locally**: </i> Suitable for those who want to debug or only have a local copy of their images. We suggest starting with a local build before making any changes to the repository, as the build process via GitHub workflows can be slow, and pushing changes to your forked repository for debugging may not be time-efficient.

    Ensure that the mkosi tool compatible with the `sev-certify` project is installed on your system. `mkosi` installation instructions are available [here](#install-mkosi-on-your-system)

    Then, build new host/guest OS images using `mkosi` in `sev-certify` directory:

    ```
    mkosi --image-id=host-<new-os-name>-<new-os-release> \
    -C images/host-<new-os-name>-<new-os-release> build
    ```
    Host image, host Kernel, host boot ramfs are generated in the `host-<new-os-name>-<new-os-release>` directory.

    ```
    mkosi --image-id=host-<new-os-name>-<new-os-release> \
    -C images/host-<new-os-name>-<new-os-release> build
    ```
    Guest image, guest Kernel, guest boot ramfs are generated in the `guest-<new-os-name>-<new-os-release>` directory.

    Ubuntu users encountering AppArmor-related permission errors with mkosi should refer to this [exception](#exceptions).

  - **<i>Option 2: Build through workflows: </i>** Preferred for the users who plan to build their new host/guest OS images <ins>within their forked `sev-certify` GH repository</ins> after pushing their new mkosi templates.

    Pushing commits to the main branch of your forked repository will automatically build all guest and host images and publish them as assets under the `devel` release. To build images for a non‑main branch, you must manually trigger the workflow. Go to your repository’s Actions tab, select the `build-and-release` workflow, and use `Run workflow` to start the build. Check the `Replace existing development assets` box.

    Then, verify for successful execution with the built new host and guest OS images under `devel` tag.

    If the `build-and-release` workflow fails, you need to troubleshoot for the error in the workflow log, implement changes, and retest until successful execution.

- **<ins>Test the new OS Host/Guest Images:</ins>** 

  To test the built guest image, you should:

    - **<i>Retrieve the built guest image</i>** either by downloading the new guest OS image `guest-<new-os-name>-<new-os-release>` under `devel` tag of your forked repository, (or) use the locally built guest OS image `images/guest-<new-os-name>-<new-os-release>/guest-<new-os-name>-<new-os-release>.efi` on the system.
    - Launch the built guest image on SEV-enabled host following the instructions in this [guide](https://github.com/AMDEPYC/sev-certify/blob/main/docs/how-to-run-guest-manually.md).

    To test the host image, you should:

    - **<i>Fetch the built host image</i>**  either by downloading the new host OS image `host-<new-os-name>-<new-os-release>` under `devel` tag of your forked repository (or) use the locally built host OS image `images/guest-<new-os-name>-<new-os-release>/guest-<new-os-name>-<new-os-release>.efi` on the system.
    - Boot the built host image on your SNP-enabled test server using HTTPboot/dispatch process to test the newly built host OS image on bare metal and generate new OS certificate under your forked `sev-certify` issues using this [guide](https://github.com/AMDEPYC/sev-certify/blob/main/docs/how-to-generate-certs.md#forking-amdepycsev-certify-for-certification-testing). Host image will automatically reboot when done.

- **<ins>Submit a Pull Request for the new OS:</ins>** Once all tests pass, create a pull request to integrate your new OS support into the `sev-certify` upstream.

**NOTE:** Ensure that all GitHub workflow tests pass on your last pushed commit to facilitate integration into the upstream repository.

## Configure new mkosi template for Host and Guest OS images
### Host OS Image Configuration
Create a `mkosi.conf` file under `sev-certify/images/host-<new-os-name>-<new-os-release>` folder. Ensure it includes:
  ```
  [Include]
  # Include required modules in the host image
  Include=../../modules/host

  [Distribution]
  Distribution=<new-os-name>
  Release=<new-os-release>

  [Content]
  Packages=
    # Required host OS packages to certify Host OS in sev-certify
    <host-kernel-package>
    <qemu-package>
    <ovmf-package>

    # Required host OS package dependencies for sev-certify framework execution
    <systemd-package>
    <systemd-resolved-package>
    <systemd-journal-remote-package>
    <openssl-package>
    <xxd-package>
    <python3-pip-package>
    <python3-emoji-package>
    <jq-package>
    <avahi-daemon-package>
  ```

### Guest OS Image Configuration
Create a `mkosi.conf` in `sev-certify/images/guest-<new-os-name>-<new-os-release>` folder.

Sample `mkosi.conf` content for new guest OS image has below minimum requirements:
  ```
  [Include]
  # Include required modules in the guest image
  Include=../../modules/guest

  [Distribution]
  Distribution=<new-os-distro>
  Release=<new-os-release>

  [Content]
  Packages=
  # Required guest OS packages to certify Guest OS in sev-certify
  <guest-kernel-package>

  # Required guest OS package dependencies for sev-certify framework execution
  <systemd-package>
  <systemd-boot-package>
  <systemd-boot-resolved-package>
  <systemd-journal-remote-package>
  <openssl-package>
  <ca-certificates-package>
  <jq-package>
  <xxd-package>
  ```
## Install mkosi on your System
You should:
- Identify the mkosi version compatible with the sev-certify project in `Install dependencies` task from the workflow [build-and-release.yml](https://github.com/AMDEPYC/sev-certify/blob/main/.github/workflows/build-and-release.yml). For now, it uses Ubuntu Plucky release version 25.3.
- Install mkosi:
  - **<i>Option 1: Install mkosi using OS package manager</i>**: Use your OS package manager to install a compatible mkosi version.  A list of supported Linux Distributions for mkosi package can be found under `tags` on the [mkosi GitHub page](https://github.com/systemd/mkosi). Alternatively, you can build from the source following Option 2 instructions.
  - **<i>Option 2: Install mkosi from source repository</i>**: Build and install mkosi from the source repository

    Build mkosi from source and update `MKOSI_VERSION` to match with the `mkosi` version used in the sev-certify upstream.
    ```bash
    cd /tmp
    export MKOSI_VERSION=v25.3
    git clone https://github.com/systemd/mkosi.git
    cd mkosi
    git checkout $MKOSI_VERSION
    ```

    Install it manually since mkosi might not be in all package managers by default. Inside the /tmp/mkosi directory, run the following:
    ```bash
    sudo make install
    ```

    After make finishes, optionally move the mkosi script. For example:
    ```bash
    sudo mv mkosi /usr/local/bin
    ```
    Verify mkosi installation with:
    ```bash
    mkosi --version
    ```
    The displayed version should match the version used in sev-certify upstream.

## Build Artifacts: Manifest and Changelog Files

When building OS images with mkosi, two additional metadata files are generated alongside the image: the **manifest** file and the **changelog** file. These files provide detailed information about the packages included in the built image for traceability, debugging, and compliance purposes.

### Manifest File (`<image-name>.manifest`)

The manifest file is a JSON document that captures the complete list of packages installed in the image at build time. It serves as a Software Bill of Materials (SBOM) for the image.

**Key Attributes:**

| Attribute | Description |
|-----------|-------------|
| `manifest_version` | Version number of the manifest format |
| `config.name` | Name of the built image (e.g., `guest-rocky-10`) |
| `config.distribution` | Linux distribution name (e.g., `rocky`, `fedora`, `ubuntu`) |
| `config.architecture` | Target CPU architecture (e.g., `x86-64`) |
| `config.output_format` | Output format of the image (e.g., `uki` for Unified Kernel Image) |
| `config.release` | Distribution release version |
| `packages` | Array of installed packages, each containing `type`, `name`, `version`, and `architecture` |
| `extension` | Reserved for future extensibility |

**Use Cases:**
- **Reproducibility:** Verify exactly which package versions are included in a specific image build
- **Security Audits:** Identify vulnerable packages by cross-referencing with CVE databases
- **Compliance:** Provide evidence of software components for regulatory requirements
- **Debugging:** Troubleshoot issues by comparing package versions across different builds

### Changelog File (`<image-name>.changelog`)

The changelog file contains the aggregated changelog entries from all packages installed in the image. It provides a detailed history of changes for each package.

**Key Attributes:**

| Attribute | Description |
|-----------|-------------|
| `Packages` | Total count of packages in the image |
| `Size` | Total size of all packages in bytes |
| `SourcePackage` | Source RPM/DEB package name for each package |
| `Changelog` | Chronological list of changes with dates, authors, versions, and descriptions |

**Use Cases:**
- **Change Tracking:** Understand what modifications were made to packages between releases
- **Issue Investigation:** Trace when specific fixes or features were introduced
- **Release Notes:** Generate documentation about changes included in an image update
- **Audit Trail:** Maintain records of package evolution for compliance purposes

## Exceptions

### Important Note for Ubuntu Users
Permission errors related to AppArmor while running mkosi are recognized and documented in [systemd/mkosi#3265](https://github.com/systemd/mkosi/issues/3265). Please take a look at this issue to tackle this permission error.

## References
- **<i>Debian based OS:</i>** Sample PR to add Ubuntu 25.10 OS Support into the `sev-certify` project is available in this [link](https://github.com/AMDEPYC/sev-certify/pull/222).
- **<i>RH based OS:</i>** Sample PR to add Rocky 10 OS Support into the `sev-certify` project is available in this [link](https://github.com/AMDEPYC/sev-certify/pull/87/).
