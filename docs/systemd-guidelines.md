# Terminology

"Targets" (.target files) define/establish "stages", for example, boot, test and report stages.<br>
"Barrier services" are closely related to targets, but allow targets to be decoupled from stage details. "Barriers" and "barrier services" are used interchangeably.<br>
"Worker services" are services that we create, the .service file and the service code. "Workers" and "worker services" are used interchangeably.<br>

# Overview

## Activation versus ordering versus enrollment 

Activation of a systemd unit happens via

- systemctl start (or restart)<br>
- Wants/Requires<br>
- WantedBy/RequiredBy plus enabling via enable directive .preset file or systemctl enable<br>

For sev-certify, somehow automating systemctl start versus one of the other activation methods doesn't make sense. Also, the "directions" of Wants/Requires and WantedBy/RequiredBy are opposite and it may only be appropriate/correct to change "one side". For example, it's inappropriate to change multi-user.target to have Wants/Requires=`<`one or more sev-certify units`>`. WantedBy/RequiredBy "enrolls" a unit and this plus enabling is one way to activate.<br>

Ordering is only achieved via Before/After directives in unit files.

## Value of having both targets and "barrier services"

(straight from Claude Code)

1. Stage chaining stays stable — targets give each stage a named boundary that subsequent stages reference. As workers are added or removed from a stage, only the barrier changes (Requires=); the target and the chain above it are untouched.
2. Intra-stage ordering without coupling — when workers within a stage must run in sequence, a started barrier gives them a common synchronization point without workers needing to reference each other directly. Without the barrier, you'd have to wire workers to each other, coupling units that conceptually belong to the same stage independently.

# The target - barrier service pattern

Each target Requires= and After= the previous target. After= the previous target helps provide inter-stage ordering. Each target also Wants= and After= its barrier service. For example, in report.target:<br> 

Requires=test.target<br>
After=test.target<br>
Wants=report-done.service<br>
After=report-done.service<br>

A target has no ExecStart, so without this second After= and even though targets have After=`<`previous target`>`, all the targets would activate at essentially the same time and, as a result, be out of sync with the stage workers. 

Each barrier service Requires= and After= all of its worker services. A barrier does have an ExecStart, but the simplest, most natural way for a barrier to stay in sync with its workers is to After= all of the workers. For example, in guest report-done.service:<br>

Requires=display-guest-logs.service sev-certificate-generator.service<br>
After=display-guest-logs.service sev-certificate-generator.service<br>

# Intra-stage ordering

In cases where intra-stage ordering is required, worker services use After= to achieve it. This works for oneshot services. For non-oneshot, either<br>

1) have a oneshot service use a non-systemd mechanism to tell when the non-oneshot is done and use After= with this oneshot service or 
2) use OnSuccess (and OnFailure?). 

An example of 1) is the verify-guest service (Type=oneshot) checking logs to determine when the launch-guest service (Type=simple) is done.<br>

# Bootstrapping

Stop targets (guest and host) have WantedBy=multi-user.target. multi-user.target is the "system is ready" terminal target — always present, always reached on normal boot. This is the only use of WantedBy that's required in sev-certify. This "enrollment" is not enough to "activate" the stop targets. Activation of the stop targets requires enabling (or starting) them. Do this via an "enable stop.target" directive in a .preset file.<br>

# General systemd units

These are units for which we don't maintain either unit files (.service, .target, etc.) or "unit code". For simplicity and clarity, it's best to reference them in targets, via Requires/Wants/After. This should be done as early as possible, for example, Requires= and After= in boot.target.<br>

# Other directives

## Type

In sev-certify, use `Type=oneshot` with `RemainAfterExit=yes` when a suitable `TimeoutStartSec` value can be determined. Otherwise, use `Type=simple`.<br>

You can't easily use Before/After with simple services since they satisfy Before/After as soon as they start. See intra-stage ordering above. With oneshot services, Before/After isn't satisfied until the main process exits.<br>

With oneshot services, `TimeoutStartSec` is how long the main process has to exit/finish before systemd kills it. This can affect subprocesses and whether it does depends on `RemainAfterExit` and `KillMode` directives.<br>

default: simple<br>

## RemainAfterExit

In sev-certify, use `RemainAfterExit=yes` with oneshot services and `RemainAfterExit=no`, the default, with simple services.<br>

`RemainAfterExit` has the same semantics for oneshot and simple services. `RemainAfterExit=no` (default) means the service will stop when the main process exits. `RemainAfterExit=yes` means the service will stay active after the main process exits.<br>

Simple services are expected to keep running so `RemainAfterExit=yes` is much less common with them than with oneshot services. (For simple services, `RemainAfterExit=yes` normally has no effect and can mask exit-causing errors.)<br>

default: no<br>

## DefaultDependencies

In sev-certify, use `DefaultDependencies=no`.<br>

`DefaultDependencies=no` allows precise, self-contained placement of a unit in the dependency graph. systemd units in sev-certify aren't standard and default dependencies don't make sense for them.<br>

default: yes<br>

## KillMode

`KillMode` controls which processes systemd will kill when a unit is stopped.<br>

For sev-certify, it's better to use `RemainAfterExit=yes` to avoid undesired process killing than to change `KillMode` from control-group, its default.<br>

default: control-group<br>

## TimeoutStartSec

See above. Also, `TimeoutStartSec=infinity` is how to express no timeout.<br>

default: 90s<br>

