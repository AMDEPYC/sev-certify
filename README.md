# Welcome to SEV OS Certification


The purpose of this repository is to provide a unified framework for testing and certifying operating system support for [AMD Secure Encrypted Virtualization (SEV)](https://www.amd.com/en/developer/sev.html) features. These are hardware-enabled security features that provide confidentiality and integrity of VM memory through per-VM encryption keys. Self-service tools are provided to run a series of certification tests using an AMD EPYC server, allowing for any user/organization to verify SEV support on a particular OS. 

**Note**: Currently only linux distributions supported by [`mkosi`](https://github.com/systemd/mkosi) are compatible with this framework.

## Certification Matrix

This table contains operating systems that have undergone certification testing for AMD features through this repository. 

| OS |  Status |  Certification Level |
|---|---|---|
| Ubuntu 25.04 |  ✅ |  [v3.0-0](https://github.com/AMDEPYC/sev-certify/issues/154) |
| Debian 13 |  ❌ |  [N/A](https://github.com/AMDEPYC/sev-certify/issues/152) |
| Fedora 41 |  ✅ |  [v3.0-0](https://github.com/AMDEPYC/sev-certify/issues/153) |
| CentOS 10 |  ✅ |  [v3.0-0](https://github.com/AMDEPYC/sev-certify/issues/151) |
| Rocky 10.0 |  ❌ |  N/A |

✅ Passing tests for latest certification level
❌ Not Certified for latest level

## Self-Service Certification Tools


Users/Organizations may target their own SEV-enabled EPYC server for self-service certification runs. Follow our guide on running an automated certification test [here](https://github.com/AMDEPYC/sev-certify/blob/main/docs/how-to-generate-certs.md).

## Certification Result Information

Each certification run automatically creates a GitHub Issue containing the results and assigning a certification level. Issues are tagged by OS and SEV feature to facilitate searching and tracking.

_Issue tags and details to be added here._

## Images


Host and Guest images are constructed in GitHub Workflows via [`mkosi`](https://github.com/systemd/mkosi). Host images are designed to be booted on a SEV-enabled EPYC server, and are configured with a series of tests in the form of custom systemd services that will run on an embedded guest image. The resulting host and guest images are available in GitHub releases.


