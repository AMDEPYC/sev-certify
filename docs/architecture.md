# Shared architecture notes (sev-certify → sev-performance)

This document captures **shared design/implementation context** that existed before `sev-performance` (perf) was forked from `AMDEPYC/sev-certify`.

It’s intended to be:

- **Short and stable** (concepts, not step-by-step recipes)
- Useful to both repos
- A landing page that we can eventually move upstream to `sev-certify` once it settles

## Why a workflow exists at all

Both projects need a repeatable way to produce **bootable host and guest OS images** that:

- are composed from many mkosi modules
- can be rebuilt consistently across multiple distros
- can be published in a way other systems/tools can consume (for example, a dispatch-style runner)

GitHub Actions is used mainly because it provides a consistent build environment and a built-in artifact distribution mechanism.

## mkosi images, host/guest relationship

At a high level:

- **Guest image**: a bootable UKI (`.efi`) that will run inside QEMU/KVM.
- **Host image**: a bootable UKI (`.efi`) intended for bare metal.

The host image usually embeds the guest UKI (so the host can launch the guest without downloading anything at runtime).

## Release assets as the distribution format

The build workflow typically publishes the produced `.efi` files as **GitHub Release Assets**.

Other tooling (for example, a dispatch-like runner) can then download a specific release tag and serve/boot those assets.

## Batch-mode operation via systemd

The images are designed to run “headless”:

- systemd units enforce ordering (for example, “host is ready” → “launch guest” → “collect results”)
- failures are captured in journald
- the result is something that can run unattended and still leave behind a useful trace

Perf-specific note: perf’s orchestration differs from sev-certify (for example, multi-guest support, workloads), but the **systemd-first** operating model is inherited.

## Logging pipeline (guest → host)

A common pattern is to forward **guest journald** logs to the host so you can inspect guest behavior without needing SSH.

In perf, see `docs/modules/logging.md` for the implementation details.

## Results persistence (Issues) — unstable

Historically, sev-certify used a “results as GitHub Issues” approach to persist run outputs.

Perf currently still has related plumbing/code paths, but this is expected to change as:

- perf stakeholders decide what “results” should look like for performance workloads
- benchmark output formats evolve toward more consistent machine-readable summaries

If perf stops opening issues, this section should remain a short historical note.

## Where to look next

- Certification/dispatch-style running: `docs/how-to-generate-certs.md` (stub)
- Manual guest launching (debugging): `docs/how-to-run-guest-manually.md` (stub)
- perf-specific guest orchestration: `docs/modules/launch-snp-guest.md`
- perf-specific containerized workloads: `docs/modules/workload-runner.md`
