# Enabling AMD Security Features in AMD EPYC Processors

## SEV Introduction
When a virtual machine (VM) starts, data is loaded into system memory (RAM). The data can be vulnerable to software or hardware probing by attackers on the host system- especially in shared environments like cloud platforms, where multiple tenants share the same physical resources. To mitigate this risk, users must ensure that the data in RAM is protected from both attackers and hypervisors. Doing so reduces the level of trust that virtual machines need to place in the hypervisor and the host system's administrators.

AMD EPYC processors introduce confidential computing technologies that provide memory encryption for virtualized environments, protecting data not only from physical attacks but also from other virtual machines and even the hypervisor itself.

The following sections describe the different generations of Secure Encrypted Virtualization (SEV), each building on the previous generation and introducing new security capabilities and features:

**SEV (Secure Encrypted Virtualization)**  is the first generation of the security features. It protects KVM virtual machines (VMs) by transparently encrypting the memory of the VM using a unique key.

**ES (Encrypted State)** is the second generation of SEV. It adds CPU register encryption when a VM stops running, preventing the information leak from the CPU registers to components like the hypervisor.

**SNP (Secure Nested Paging)**  is the third generation of SEV. It adds strong memory integrity protection on top of SEV and ES to aid in preventing malicious hypervisor-based attacks(data replay, memory mapping and more) to create an isolated execution environment. SNP also introduces several additional optional security enhancements designed to support additional VM use models, offer stronger protection around interrupt behavior, and offer increased protection against recently disclosed side channel attacks. It also introduces a new attestation model that allows run-time attestation in SNP protected VMs.

## Configuring SNP
Users can utilize the following guides to set-up SNP in their system.

### 1. Host Configuration for the Host Users
Enable SNP in your host in order to launch SNP protected VMs.

#### SNP host requirements:

- AMD EPYC Processor: 7003 or newer.

- kernel version: 6.11 or newer.

#### Enable AMD's security feature(SEV) in the host BIOS
To enable SNP in BIOS you need to enable the following settings:

```
CBS -> CPU Common ->
            SEV-ES ASID Space Limit -> 100
            SNP Memory Coverage -> Enabled
            SMEE -> Enabled
    -> NBIO Common ->
            SEV-SNP -> Enabled
```
For a more in depth enablement guide, please take a look at the "Using SEV with AMD EPYC Processors" guide in our additional resources.

NOTE: The SNP options might differ depending on the server manufacturer and BIOS version. Please refer to your respective server manual to enable SNP options in the BIOS settings.

#### Verify SNP enablement

To verify the complete enablement of AMD’s security features (SEV, ES, and SNP) within their Linux host, users may utilize the [Virtee snphost](https://github.com/virtee/snphost) tool to assess SNP support and enablement on the system:
To use this tool:
1. Download the latest snphost release from [snphost GH Releases](https://github.com/virtee/snphost/releases) page.
2. Execute the command `snphost ok` to confirm the presence and status of the supported security features.

### 2. Guest Launch and enablement for the Guest Users

An SNP enabled guest can be launched after the host has properly set-up and enabled SNP.
The following are **guest** requirements to launch an SNP enabled VM:
   - Guest kernel version: 5.19+
   - QEMU version: 9.2+
   - OVMF version: 2024.11+

#### Guest Launch

Guest users may initiate SEV-SNP-enabled virtual machine boots using the QEMU hypervisor by utilizing the mainline release of one of the certified images in this repository. Please reference the table of certified images here: **[Certification Matrix](https://github.com/AMDEPYC/sev-certify#certification-matrix)**

To boot one of the mainline qcow2 images from one of the certified OS,  the user can use a command similar to the following:
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
    -object sev-snp-guest,id=sev0,cbitpos=51,reduced-phys-bits=1 kernel-hashes=on \
    -bios <amdsev-ovmf-path> \
    -hda <path-to-guest-image>
```
Users may allocate the desired amount of memory for the guest virtual machine, with a minimum requirement of 2 GB (2048 MB).

`amdsev-ovmf-path` refers to the AMDSEV UEFI compatible guest firmware located at either `/usr/share/ovmf/OVMF.amdsev.fd` or `/usr/share/edk2/ovmf/OVMF.amdsev.fd` based on your host linux distribution.

`path-to-guest-image` refers to your custom guest image file path.

Guest users can refer to SEV [QEMU documentation](https://www.qemu.org/docs/master/system/i386/amd-memory-encryption.html) for the additional SEV guest capabilities.

### 3. SEV Certificates for the Verifiers
Verifiers seek to perform AMD' SEV validation checks to confirm the presence and functionality of AMD’s Secure Encrypted Virtualization features. These verifiers may include operating system vendors, hardware manufacturers, or OEMs evaluating support within their platforms, firmware, or pre-release operating systems.

A comprehensive list of operating systems that support AMD SEV features is available in the [Certification Matrix](https://github.com/AMDEPYC/sev-certify#certification-matrix). Additionally, verifiers may review detailed host and guest SEV status reports within the GitHub Issues section of the sev-certify repository, which are automatically generated by the [dispatch](https://github.com/AMDEPYC/dispatch.git) tool.

Verifiers may generate a new SEV certificate to evaluate the status of AMD SEV features on their specific hardware, firmware, or pre-release operating system by following the guidelines highlighted in [how-to-generate-certs documentation](../docs/how-to-generate-certs.md)

## Additional Resources

[AMD Secure Encrypted Virtualization Developer Central](https://www.amd.com/en/developer/sev.html)

[Using SEV with AMD EPYC Processors](https://www.amd.com/content/dam/amd/en/documents/epyc-technical-docs/tuning-guides/58207-using-sev-with-amd-epyc-processors.pdf)