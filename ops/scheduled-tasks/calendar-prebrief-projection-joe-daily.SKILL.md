---
name: calendar-prebrief-projection-joe-daily
description: Authority-managed sponsor-bound EventKit prebrief projection contract.
---

The checked-in definition is disabled by default. Do not create, enable, or
invoke this through a generic scheduler or configuration sync. Joe activation
is a separate two-stage path: a typed authority receipt for the current
allowlist, followed by the sealed local activation command, which atomically
enables the 0600 Joe runtime profile and bootstraps the dedicated LaunchAgent.
The database claim gate rejects work unless the latest activation receipt still
matches the current allowlist. Dell and canaries remain disabled.
