# Getting Started Guide for AMD Security Feature(SEV) on AMD EPYC Processor

## SEV Introduction
When a virtual machine is started, data is loaded into memory (RAM). This makes the data vulnerable to software or hardware probing by attackers on the host system, especially in shared environments like cloud computing, where resources are shared by many tenants. For this reason, users must ensure that the data in RAM is secure and protected from both attackers and hypervisors. This reduces the amount of trust virtual machines need to place in the hypervisor and the host system's administrators.

**AMD's SEV (Secure Encrypted Virtualization)** is a technology used to protect KVM virtual machines (VMs) by transparently encrypting the memory of each VM with a unique key. SEV can also calculate a signature of the memory's content. This signature is provided to the VM's owner as an attestation to prove that the memory was correctly encrypted by the firmware.

**AMD's SEV-ES (Secure Encrypted Virtualization - Encrypted State)** is a technology that encrypts all CPU register contents when a VM halts running, preventing the information leak from the CPU registers to components like hypervisor.

**AMD's SEV-SNP (AMD Secure Encrypted Virtualization-Secure Nested Paging)** is a technology which adds strong memory integrity protection on top of AMD's SEV and SEV-ES to aid in preventing malicious hypervisor-based attacks(data replay, memory mapping and so on) to create an isolated execution environment.

Resources
[AMD Secure Encrypted Virtualization Developer Central](https://www.amd.com/en/developer/sev.html)
[AMD-SEV Guide on SUSE Linux Enterprise Server 15 SP7 distribution](https://documentation.suse.com/sles/15-SP7/html/SLES-amd-sev/article-amd-sev.html#:~:text=AMD's%20Secure%20Encrypted%20Virtualization%20(SEV,virtual%20machine's%20CPU%20register%20content.))

## User-Specific SEV Implementation Guide
Stakeholders with varying objectives can utilize the following user guides to begin their implementation of AMD's SEV.

### 1. Host
Host system users can configure AMD's Secure Encrypted Virtualization (SEV) and subsequently verify its enablement within their specific Linux environment.

#### Enable AMD's security feature(SEV) in the host BIOS
The host hardware should support AMD's SEV technology and should be enabled in the server BIOS.

Host users should enable AMD Secure Memory Encryption (SMEE) feature in BIOS on the host hardware containing AMD EPYC processors. Follow the instructions posted in [Using SEV with AMD EPYC Processors](https://www.amd.com/content/dam/amd/en/documents/epyc-technical-docs/tuning-guides/58207-using-sev-with-amd-epyc-processors.pdf) to enable AMD SEV features from BIOS.

#### Verify for AMD's security feature enablement on the host
Host users are advised to manually verify the enablement of AMD’s security features (SEV, SEV-ES, and SEV-SNP) within their Linux host environment. To facilitate this verification, users may utilize the snphost tool to assess SEV-SNP support on the system:
- Download the latest snphost release from [snphost GH Releases](https://github.com/virtee/snphost/releases) page.
- Execute the command `snphost ok` to confirm the presence and status of the supported security features.

### 2. Guest
Guest users can launch SNP-enabled QEMU guest on the SNP host.
**Host Requirements:**
- Guest users should ensure that the below required packages are installed on the host:
   - kernel package version: 6.11+
   - QEMU version: 9.2+
   - OVMF version: 2024.11+

**Procedure:**
Guest users may initiate SEV-SNP-enabled virtual machine boots using the QEMU hypervisor by either utilizing the guest UKI artifacts provided in the [sev-certify](https://github.com/AMDEPYC/sev-certify.git) project or deploying their own custom guest image.

Guest users have two options for launching an SEV-SNP-enabled virtual machine using QEMU:
- **Option 1:** Download or build guest artifacts tailored to their specific operating system distribution from the [sev-certify](https://github.com/AMDEPYC/sev-certify.git) project. These artifacts can then be used to initiate an SEV-SNP-enabled guest following the procedures outlined in the [how-to-run-guest-manually](https://github.com/AMDEPYC/sev-certify/blob/main/docs/how-to-run-guest-manually.md) guide.

- **Option 2**: Directly boot an SEV-SNP-enabled guest using a custom guest image by specifying the appropriate QEMU command-line parameters.
```sh
$ qemu-system-x86_64 \
  -enable-kvm \
  -machine q35 \
  -cpu EPYC-v4 \
  -machine memory-encryption=sev0 \
  -monitor none \
  -display none \
  -object memory-backend-memfd,id=ram1,size=<guest-ram-size> \
  -machine memory-backend=ram1 \
  -object sev-snp-guest,id=sev0,cbitpos=51,reduced-phys-bits=1 kernel-hashes=on" \
  -bios <amdsev-ovmf-path> \
  -kernel <guest-user-image-path>
```
Users may allocate the desired amount of memory for the guest virtual machine, with a minimum requirement of 2 GB (2048 MB).

`amdsev-ovmf-path` refers to the AMDSEV UEFI compatible guest firmware located at either `/usr/share/ovmf/OVMF.amdsev.fd` or `/usr/share/edk2/ovmf/OVMF.amdsev.fd` based on your host linux distribution.

`guest-user-image-path` refers to your custom guest image file path.

Guest users can refer to [QEMU documentation](https://www.qemu.org/documentation/) for the additional guest capabilities.

### 3. Verifier
Verifiers seek to perform AMD' SEV validation checks to confirm the presence and functionality of AMD’s Secure Encrypted Virtualization features. These verifiers may include operating system vendors, hardware manufacturers, or OEMs evaluating support within their platforms, firmware, or pre-release operating systems.

A comprehensive list of operating systems that support AMD SEV features is available in the [Certification Matrix](https://github.com/AMDEPYC/sev-certify#certification-matrix). Additionally, verifiers may review detailed host and guest SEV status reports within the GitHub Issues section of the sev-certify repository, which are automatically generated by the [dispatch](https://github.com/AMDEPYC/dispatch.git) tool.

**Procedure**
Verifiers may generate a new SEV certificate to evaluate the status of AMD SEV features on their specific hardware, firmware, or pre-release operating system. The process involves the following steps:
- **Fork the [sev-certify](https://github.com/AMDEPYC/sev-certify.git) repository** to create a personalized workspace for validation.
- **Operating system vendors** intending to test a new pre-release should incorporate support for their OS version within their fork of the [sev-certify](https://github.com/AMDEPYC/sev-certify.git) repository. This is achieved by creating a corresponding `mkosi.conf` configuration file using the mkosi tool, and placing it under the `images/` directory to define host and guest image parameters.

- **Verify the presence of the newly added host and guest artifacts** under the `Development Images` release tag in the forked sev-certify repository.

- **Set up and execute the [dispatch](https://github.com/AMDEPYC/dispatch.git) tool** against the development branch of the forked repository. Instructions for configuring and running the dispatch tool with the current host artifacts can be found [here](https://github.com/AMDEPYC/sev-certify/blob/main/docs/how-to-generate-certs.md).

To validate a new OS pre-release, verifiers can run the dispatch tool on your `sev-certify` fork using the following command::
```sh
./dispatch --owner <your GH username> --repo sev-certify <your-new-os-pre-release>
```

Alternatively, to download and utilize all existing host artifacts from your `sev-certify` fork, the following command may be used:
```sh
./dispatch --owner <your GH username> --repo sev-certify
```

- **Review the new sev-certificate** by examining the newly generated GitHub issues under the forked sev-certify repository, which detail the AMD's SEV feature status and validation outcomes.