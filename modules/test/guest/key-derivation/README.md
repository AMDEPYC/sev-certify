# SNP Guest Key Derivation Tests

This guest-image module includes a Python-based systemd service that executes comprehensive key derivation tests on SNP-enabled guests using the [snpguest tool](https://github.com/virtee/snpguest.git).

## Test Coverage

The test suite validates the following key derivation properties:

1. **Determinism**: Same parameters produce the same key
2. **VMPL Isolation**: Different VMPL values produce different keys (cryptographic isolation)
3. **Root Key Difference**: VCK and VMRK produce different keys
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
- **VCK (Versioned Chip Key)**: Symmetric key derived from CEK+TCB
- **VMRK (VM Root Key)**: VM-specific symmetric key for migration scenarios

Note: Despite the name "vcek" in the CLI, the root key selection uses **VCK** (symmetric), not VCEK (asymmetric signing key).

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
# Run the test suite
/usr/local/lib/scripts/snpguest_key_derivation.py

# View test results
journalctl -u key-derivation.service

# Check test status log
cat /usr/local/lib/key_derivation_status
```

### Test Output

Each test produces:
- Status log in JSON format: `/usr/local/lib/key_derivation_status`
- Derived keys stored in: `/usr/local/lib/key_derivation_service/`
- Console output with pass/fail status and key values

### Expected Output

Successful test run:

```
======================================================================
SNP Guest Key Derivation Test Suite
======================================================================

======================================================================
TEST: Key Derivation Determinism
======================================================================
✓ PASS: Keys match (deterministic)
  Key: 0x<hex>

======================================================================
TEST: VMPL-Based Key Isolation
======================================================================
✓ PASS: VMPL0 and VMPL1 keys differ (proper isolation)
  VMPL0 Key: 0x<hex>
  VMPL1 Key: 0x<hex>

...

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

## Implementation Notes

### Python vs Bash

Unlike the attestation tests which use bash, this module uses Python for:
- Better error handling and structured output
- Type safety and code clarity
- Easier maintenance and extension
- Native JSON handling for status logs

### VMPL Constraints

The VMPL isolation test may produce warnings if running at VMPL > 0, as the firmware enforces that derived keys can only be requested for VMPL values ≥ current VMPL. This is expected behavior and validates the security constraint.

### Key Display

The tests use `snpguest display key` to read derived keys as hex strings for comparison. This avoids binary file comparison issues and provides human-readable output.

## Integration with sev-certify

To include key-derivation tests in the guest build, update the parent `mkosi.conf`:

```conf
[Include]
Include=./attestation-result
Include=./attestation-workflow
Include=./key-derivation        # Add this line
Include=./test-done
```

## References

- [CLAUDE.md](../../../../../CLAUDE.md) - VCK/VCEK naming clarification
- [SEV-SNP-ARCHITECTURE.md](../../../../../SEV-SNP-ARCHITECTURE.md) - VMPL isolation details
- [snpguest documentation](https://github.com/virtee/snpguest) - Key derivation API
