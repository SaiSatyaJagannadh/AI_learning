---
name: expiry-and-config
description: Failures that start at a wall-clock instant with no load change - expired certs, rotated keys, elapsed TTLs.
tools: search_logs, timeline
---

## When to use this

Everything was healthy, then at one precise timestamp nothing works, and
traffic never changed. Time itself is the trigger.

## Procedure

1. `search_logs` for `expir`, `notAfter`, `x509`, `handshake`, `token`,
   `unauthorized`, `certificate`. The truncated stem `expir` catches
   "expires", "expired", and "expiry" in one pass.
2. Look for the **warning before the failure**. Expiries are nearly always
   announced — a WARN a minute or an hour earlier naming the exact deadline.
   Finding it converts "something broke" into "we were told and missed it".
3. `timeline` around the deadline to confirm the failures begin at it rather
   than near it.

## What to look for

An exact `notAfter` / `expires_at` timestamp in the log. Quote it. It is the
difference between a diagnosis and a guess, and it is what makes the remedy
obvious.

## How to state the answer

Name the artifact (which certificate, which key, for which CN), the exact
expiry timestamp, and the remedy — renew and reload — rather than a restart,
which will not help.
