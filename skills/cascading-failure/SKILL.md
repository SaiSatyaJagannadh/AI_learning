---
name: cascading-failure
description: A downstream error storm (502s, timeouts) whose real cause is an upstream change minutes earlier in a different log.
tools: list_logs, search_logs, timeline
---

## When to use this

The symptom is loud, late, and in the wrong file. Gateway 502s, upstream
timeouts, "could not acquire connection". The log that is screaming is almost
never the log that broke.

## Procedure

1. `list_logs` — know what files exist before assuming which one matters.
2. `search_logs` for the symptom in the file that reports it (`502`,
   `timed out`, `refused`). You want **the first occurrence**, not the count.
   The first bad timestamp is the only one that carries information; the other
   four hundred are the same event repeated.
3. `timeline` with `around` set to that first timestamp, `window` a few
   minutes, and **every** log file in `files`. This is the whole trick: the
   cause and the effect are adjacent in time and far apart in filename.
4. Read the timeline from the top. The last calm line before the storm, in a
   *different* file, is your candidate.

## What to look for

Config changes logged at INFO. This is the trap: severity filtering finds the
symptom and hides the cause, because nobody logs "I am about to break
production" at ERROR. `DB_POOL_SIZE increased from 10 to 100` is an INFO line
sitting two minutes before a wall of ERRORs.

## How to state the answer

Name the change, the file, the timestamp, and the causal chain in one
sentence: change → resource limit → downstream timeout → user-visible error.
"There are 502s in the gateway" is a restatement of the question, not a
diagnosis.
