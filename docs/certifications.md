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
| CentOS 10 | ✅ | [c3.0.0-0](https://github.com/AMDEPYC/sev-certify/issues/225) |
| Debian 13 |  ❌ |  [N/A](https://github.com/AMDEPYC/sev-certify/issues/152) |
| Debian Forky | ✅ | [c3.0.0-0](https://github.com/AMDEPYC/sev-certify/issues/228) |
| Fedora 41 | ✅ | [c3.0.0-0](https://github.com/AMDEPYC/sev-certify/issues/229) |
| Rocky 10.0 |  ❌ |  N/A |
| Rocky 10.1 | ✅ | [c3.0.0-0](https://github.com/AMDEPYC/sev-certify/issues/230) |
| Ubuntu 25.04 | ✅ | [c3.0.0-0](https://github.com/AMDEPYC/sev-certify/issues/231) |
| Ubuntu 25.10 | ✅ | [c3.0.0-0](https://github.com/AMDEPYC/sev-certify/issues/232) |

AMD EPYC 9004 (Genoa)
-------------
| OS |  Status |  3.0 Certification | 3.1 Certification |
|---|---|---|---|

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
| 3.0.0-1 | memfd numa, key derivation, vlek loading, snphost config, snphost commit |
| 3.1.1-0 | Memory Hotplug, Vector Mitigation, Cloud Hypervisor |