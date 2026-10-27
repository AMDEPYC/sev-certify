Table headers describe the hardware on which the certification test was run.
Tables build upon the previous generation to include testing for backwards
compatibility of past hardware features.

# Contents
- [Certification Levels by Hardware](#certification-levels-by-hardware)
- [Certification Level Definitions](#certification-level-definitions)

# Certification Levels by Hardware

AMD EPYC 7003 (Milan)
-------------
| OS |  Status |  3.0 Certification |
|---|---|---|
| CentOS 10 | ✅ | [c3.0.0-1](https://github.com/AMDEPYC/sev-certify/issues/350) |
| Debian 13 |  ❌ |  [N/A](https://github.com/AMDEPYC/sev-certify/issues/152) |
| Debian Forky | ✅ | [c3.0.0-1](https://github.com/AMDEPYC/sev-certify/issues/348) |
| Fedora 41 | ✅ | [c3.0.0-1](https://github.com/AMDEPYC/sev-certify/issues/353) |
| Fedora 44 | ✅ | [c3.0.0-1](https://github.com/AMDEPYC/sev-certify/issues/352) |
| Opensuse leap 16.0 | ✅ | [c3.0.0-0](https://github.com/AMDEPYC/sev-certify/issues/357) |
| Rocky 10.0 |  ❌ |  N/A |
| Rocky 10.1 | ✅ | [c3.0.0-0](https://github.com/AMDEPYC/sev-certify/issues/230) |
| Rocky 10.2 | ✅ | [c3.0.0-1](https://github.com/AMDEPYC/sev-certify/issues/355) |
| Ubuntu 25.04 | ✅ | [c3.0.0-1](https://github.com/AMDEPYC/sev-certify/issues/346) |
| Ubuntu 25.10 | ✅ | [c3.0.0-1](https://github.com/AMDEPYC/sev-certify/issues/347) |
| Ubuntu 26.04 | ✅ | [c3.0.0-1](https://github.com/AMDEPYC/sev-certify/issues/351) |

AMD EPYC 9004 (Genoa)
-------------
| OS |  Status |  3.0 Certification | 3.1 Certification |
|---|---|---|---|
| Centos 10 | ✅ | [c3.0.0-1](https://github.com/AMDEPYC/sev-certify/issues/331) |
| Debian forky | ✅ | [c3.0.0-1](https://github.com/AMDEPYC/sev-certify/issues/333) |
| Fedora 41 | ✅ | [c3.0.0-1](https://github.com/AMDEPYC/sev-certify/issues/334) |
| Fedora 44 | ✅ | [c3.0.0-0](https://github.com/AMDEPYC/sev-certify/issues/324) |
| Opensuse leap 16.0 | ✅ | [c3.0.0-0](https://github.com/AMDEPYC/sev-certify/issues/336) |
| Rocky 10.2 | ✅ | [c3.0.0-1](https://github.com/AMDEPYC/sev-certify/issues/337) |
| Ubuntu 25.04 | ✅ | [c3.0.0-1](https://github.com/AMDEPYC/sev-certify/issues/338) |
| Ubuntu 25.10 | ✅ | [c3.0.0-0](https://github.com/AMDEPYC/sev-certify/issues/328) |
| Ubuntu 26.04 | ✅ | [c3.0.0-1](https://github.com/AMDEPYC/sev-certify/issues/340) |

AMD EPYC 8005 (Sorano)
-------------
| OS |  Status |  3.0 Certification | 3.1 Certification | 4.0 Certification |
|---|---|---|---|---|

AMD EPYC 9005 (Turin)
-------------
| OS |  Status |  3.0 Certification | 3.1 Certification | 4.0 Certification | 4.1 Certification |
|---|---|---|---|---|---|

# Certification Level Definitions

| Level | Features Certified |
|---|---|
| 3.0.0-0 | SEV-SNP Attestation |
| 3.0.0-1 | memfd numa, [key derivation](features/key-derivation.md), vlek loading, [snphost config & commit](features/tcb-config-commit.md) |
| 3.1.1-0 | Memory Hotplug, Vector Mitigation, Cloud Hypervisor |
| 4.1.0-0 | [Ciphertext hiding](features/ciphertext-hiding.md) |