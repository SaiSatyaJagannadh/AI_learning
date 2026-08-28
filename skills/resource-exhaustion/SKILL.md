---
name: resource-exhaustion
description: Something finite ran out - disk, memory, threads, connections - and every downstream failure is a symptom of the same limit.
tools: search_logs, timeline, log_stats
---

## When to use this

Failures that arrive in a wall rather than a trickle, and that mention a
number with a ceiling: `100%`, `50/50`, `32 of 32`, `ENOSPC`, `OOM`.

## Procedure

1. `search_logs` for the limit language, not the failure language. Try
   `ENOSPC`, `No space left`, `OOM`, `max_connections`, `pool`, `exhausted`,
   `rejected`. One of these hits the resource by name.
2. Find where the metric **crosses the line**, not where it is already over.
   A log full of `100%` tells you nothing; the line where it went from 97% to
   100% tells you when and how fast.
3. `timeline` around the crossing to confirm the failures start *after* it and
   not before. If the failures came first, the resource is a symptom too and
   you are one level too shallow — go back to step 1 with what the failures
   name instead.

## What to look for

- **Growth rate before the ceiling.** Steady climb = leak. Step change = a
  deploy or a config edit. The shape names the cause.
- **Restart loops.** A process killed and restarted straight back into the
  same slope is a leak, not a one-off. Say so explicitly, because the fix
  differs: a restart is not a remedy for a leak.

## How to state the answer

Name the resource, its limit, the time it was hit, and what consumed it.
"The service ran out of memory" is incomplete without "because entries in the
session registry were never evicted".
