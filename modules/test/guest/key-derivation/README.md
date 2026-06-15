# SNP Guest Key Derivation Tests

This guest-image module includes a Python-based systemd service that executes comprehensive key derivation tests on SNP-enabled guests using the [snpguest tool](https://github.com/virtee/snpguest.git).

## Test Coverage

The test suite validates the following key derivation properties:

1. **Determinism**: Same parameters produce the same key
2. **VMPL Isolation**: Different VMPL values produce different keys (cryptographic isolation)
3. **Root Key Difference**: VCEK and VMRK root key selections produce different keys
4. **Guest SVN Sensitivity**: Different guest SVN values produce different keys
5. **TCB Sensitivity**: Different TCB versions produce different keys
6. **Guest Field Select Sensitivity**: Different guest field select values produce different keys

## Key Derivation Security Properties Tested

### VMPL-Based Key Isolation

The tests verify that the firmware correctly implements VMPL-based cryptographic isolation:
- Code at VMPL0 can derive keys tagged vmpl=0,1,2,3
- Code at VMPL1 can derive keys tagged vmpl=1,2,3 (but NOT vmpl=0)
- Keys derived with different VMPL values are cryptographically distinct

This prevents privilege escalation where compromised guest OS at VMPL1 attempts to access SVSM secrets at VMPL0.

### Root Key Selection

Tests validate that different root keys produce different derived keys:
- **VCEK (Versioned Chip Endorsement Key)**: Selected via `RootKeySelect=0` in `SNP_DERIVE_KEY`
- **VMRK (VM Root Key)**: VM-specific key for migration scenarios; selected via `RootKeySelect=1`

AMD uses the name VCEK for two distinct roles: the asymmetric key that signs attestation reports,
and as the name for `RootKeySelect=0` in `SNP_DERIVE_KEY`. These are different uses of the same
underlying key material. The `snpguest` CLI follows AMD's naming directly.

The output of `SNP_DERIVE_KEY` is a symmetric secret returned to the guest. Whether to call it
a "key" or a "seed" is somewhat in the eye of the beholder: the firmware does not use it
internally for encryption, decryption, or authentication — it is simply derived and handed to
the guest, which then uses it as a key for its own purposes. AMD and `snpguest` call it a derived
key, reflecting its intended use.

VMRK is used in live migration to protect VM state across hosts, meaning the firmware may use it
internally for encryption or authentication — which places it more firmly in the "key" category.

## Test Architecture

The test suite is implemented in Python and follows the same structure as the attestation tests:

```
key-derivation/
├── README.md
└── mkosi.extra/
    └── usr/local/lib/
        ├── scripts/
        │   └── snpguest_key_derivation.py   # Python test implementation
        └── systemd/system/
            └── key-derivation.service        # Systemd service unit
```

## Running the Tests

### Prerequisites

The tests can only run inside an SNP-enabled guest VM with:
- `/dev/sev-guest` device available
- `snpguest` tool installed
- Python 3.x available

### Execution

The tests run automatically via systemd service after boot. Manual execution:

```bash
# Run the test suite (pass/fail output only)
/usr/local/lib/scripts/snpguest_key_derivation.py

# Run with verbose output (snpguest commands, key hex values, full attestation report)
/usr/local/lib/scripts/snpguest_key_derivation.py --debug

# Derive a key for every valid GFS value (0x00-0x7f) and report which produce distinct keys
/usr/local/lib/scripts/snpguest_key_derivation.py --gfs-sweep

# View test results
journalctl -u key-derivation.service

# Check test status log
cat /usr/local/lib/key_derivation_status
```

### Test Output

Each test produces:
- Status log in JSON format: `/usr/local/lib/key_derivation_status`
- Derived keys stored in: `/usr/local/lib/key_derivation_service/`
- Console output with pass/fail status (key hex values shown only with `--debug`)

### Expected Output

What follows is an example of a successful test run (default, no `--debug`):

```
======================================================================
ATTESTATION REPORT (reference values for key derivation bounds)
======================================================================
  Guest SVN:     1 (upper bound for --guest_svn)
  Current  TCB:  bl=0x07 tee=0x00 snp=0x0b mc=0x16
  Committed TCB: bl=0x07 tee=0x00 snp=0x0b mc=0x16 (upper bound per component for --tcb_version)
  Reported TCB:  bl=0x07 tee=0x00 snp=0x0b mc=0x16
  Launch TCB:    bl=0x07 tee=0x00 snp=0x0b mc=0x16

======================================================================
TEST: Determinism
======================================================================
✓ PASS: Keys match (deterministic)

======================================================================
TEST: VMPL Isolation
======================================================================
✓ PASS: VMPL0 and VMPL1 keys differ (proper isolation)

======================================================================
TEST: Root Key Difference
======================================================================
✓ PASS: VCEK and VMRK keys differ

======================================================================
TEST: Guest SVN Sensitivity
======================================================================
  Guest SVN upper bound: 1
  Testing 2 SVN values: [0, 1]
✓ PASS: All 2 SVN values produce distinct keys

======================================================================
TEST: TCB Sensitivity
======================================================================
  Committed TCB (upper bound per component): bl=0x07 tee=0x00 snp=0x0b mc=0x16
  Testing 5 TCB candidate(s)
✓ PASS: All 5 TCB values produce distinct keys

======================================================================
TEST: Guest Field Select Sensitivity
======================================================================
✓ PASS: GFS=0x01 and GFS=0x02 keys differ

======================================================================
TEST SUMMARY
======================================================================
✓ PASS: Determinism
✓ PASS: VMPL Isolation
✓ PASS: Root Key Difference
✓ PASS: Guest SVN Sensitivity
✓ PASS: TCB Sensitivity
✓ PASS: Guest Field Select Sensitivity

Passed: 6/6

✓ All key derivation tests passed!
```

### N/A Cases

Some tests may report `✓ PASS: N/A` rather than a full result:

- **Guest SVN Sensitivity**: Reports N/A when the guest was launched without an ID block (`guest_svn=0` in the attestation report), or with an ID block that explicitly sets the Guest SVN value to zero. Either way, it leaves  only one valid SVN value to test.
- **TCB Sensitivity**: Reports N/A when all committed TCB components are zero.
- **VMPL Isolation**: Reports N/A when VMPL1 key derivation fails (expected if not running at VMPL0 or VMPL1).

## Implementation Notes

### Python vs Bash

Unlike the attestation tests which use bash, this module uses Python for:
- Better error handling and structured output
- Type safety and code clarity
- Easier maintenance and extension
- Native JSON handling for status logs

### Attestation Report at Startup

Before running tests, the script fetches an attestation report to extract the guest SVN and committed TCB values. These provide the valid upper bounds for the SVN sensitivity and TCB sensitivity tests respectively, avoiding firmware rejections from out-of-range parameter values.

### VMPL Constraints

The VMPL isolation test may produce warnings if running at VMPL > 0, as the firmware enforces that derived keys can only be requested for VMPL values ≥ current VMPL. This is expected behavior and validates the security constraint.

### Key Reading

Derived keys are read directly from the output file bytes (`.read_bytes().hex()`). Key hex values are only printed when `--debug` is active.

