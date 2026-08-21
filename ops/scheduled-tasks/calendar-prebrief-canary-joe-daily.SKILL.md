---
name: calendar-prebrief-canary-joe-daily
description: Disabled isolated Calendar prebrief canary contract for Joe.
---

This definition is disabled. Do not create, enable, or invoke any scheduler or
manual canary operation.

The isolated database destination exists, but the required isolated EventKit
permission, signing key, and sponsor-bound device evidence are absent. Until
all are installed and the isolated destination is read back, this task has no
completion signal.

If it is ever activated through the governed control-plane path, it may write
only its isolated canary event and receipt destination. It must never read,
write, or use the live Calendar prebrief projection.
