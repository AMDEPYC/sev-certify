# Welcome to SEV OS Certification


The purpose of this repository is to provide a unified framework for testing and certifying operating system support for [AMD Secure Encrypted Virtualization (SEV)](https://www.amd.com/en/developer/sev.html) features. These are hardware-enabled security features that provide confidentiality and integrity of VM memory through per-VM encryption keys. Self-service tools are provided to run a series of certification tests using an AMD EPYC server, allowing for any user/organization to verify SEV support on a particular OS. 

**Note**: Currently only linux distributions supported by [`mkosi`](https://github.com/systemd/mkosi) are compatible with this framework.

## Certification Matrix

This table contains operating systems that have undergone certification testing for AMD features through this repository. 

| OS | Status | [EPYC 7003][cert-3.0] | [EPYC 9004][cert-3.1] | [EPYC 8005][cert-4.0] | [EPYC 9005][cert-4.1] |
|---|---|---|---|---|---|
| CentOS 10 | ✅ | [c3.0.0-0](https://github.com/AMDEPYC/sev-certify/issues/225) |
| Debian 13 |  ❌ |  [N/A](https://github.com/AMDEPYC/sev-certify/issues/152) |
| Debian Forky | ✅ | [c3.0.0-0](https://github.com/AMDEPYC/sev-certify/issues/228) |
| Fedora 41 | ✅ | [c3.0.0-0](https://github.com/AMDEPYC/sev-certify/issues/229) |
| Rocky 10.1 | ✅ | [c3.0.0-0](https://github.com/AMDEPYC/sev-certify/issues/230) |
| Ubuntu 25.04 | ✅ | [c3.0.0-0](https://github.com/AMDEPYC/sev-certify/issues/231) |
| Ubuntu 25.10 | ✅ | [c3.0.0-0](https://github.com/AMDEPYC/sev-certify/issues/232) |

✅ Latest Level Certified
❌ Latest Level Not Certified
⚠️ Backwards Compatibility Issues - see [hardware tables][hardware-tables]

See [Certificate Level Definitions][cert-definitions]
for the features certified at each level.

## Self-Service Certification Tools


Users/Organizations may target their own SEV-enabled EPYC server for self-service certification runs. Follow our guide on running an automated certification test [here](https://github.com/AMDEPYC/sev-certify/blob/main/docs/how-to-generate-certs.md).

Each certification run automatically creates a GitHub Issue containing the results and assigning a certification level. Issues are tagged by OS and SEV feature to facilitate searching and tracking.

## Images

Host and Guest images are constructed in GitHub Workflows via [`mkosi`](https://github.com/systemd/mkosi). Host images are designed to be booted on a SEV-enabled EPYC server, and are configured with a series of tests in the form of custom systemd services that will run on an embedded guest image. The resulting host and guest images are available in GitHub releases.

[cert-3.0]: ./docs/certifications.md#amd-epyc-7003-milan
[cert-3.1]: ./docs/certifications.md#amd-epyc-9004-genoa
[cert-4.0]: ./docs/certifications.md#amd-epyc-7004-bergamo
[cert-4.1]: ./docs/certifications.md#amd-epyc-9005-bergamo
[hardware-tables]: ./docs/certifications.md#certification-levels-by-hardware
[cert-definitions]: ./docs/certifications.md#certification-level-definitions


