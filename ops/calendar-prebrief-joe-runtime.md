# Joe calendar prebrief runtime

`com.carr.calendar-prebrief-joe` is an uninstalled, inactive-by-default
LaunchAgent. It uses only the Joe live profile, the installed CARR Calendar
Access app, and the jobs lease. Dell and every canary definition remain
disabled.

Activation is explicit: create the 0600 provisioner profiles, select Joe
calendars through the app catalog, register the allowlist, and prove Joe
preflight. The installer only stages a recoverable app replacement and plist.
Then use `calendar-prebrief-activation.py seal-activate-joe-live` with the
evidence digest, runtime profile, and installed plist. It requires the typed
authority readback first, atomically changes the runtime profile from `false`
to `true`, and bootstraps, kickstarts, and reads back the exact LaunchAgent.

The manifest remains disabled as the bootstrap default. The sole live exception
is authority-managed: generic control-plane sync preserves it only while the
latest Joe activation receipt matches the current allowlist. A changed
allowlist fences both scheduling and claiming until a new explicit activation.
