---
name: precision-check
description: Run before answering - confirm the anomaly you found is the cause and not a loud coincidence.
tools: timeline, search_logs
---

## When to use this

Always, before you commit to a root cause. Especially when the first anomaly
you found is a dramatic-looking metric.

## The failure this prevents

Finding *an* anomaly and calling it *the* cause. Real incidents contain
several anomalies at once; most are fellow victims. A cache miss rate spiking
from 3% to 47% at the same instant as the errors is exactly as consistent with
"the cache broke everything" as with "everything broke, so the cache is being
missed" — and the second is far more common.

## The three tests

1. **Precedence.** Does the candidate cause appear *strictly before* the first
   symptom? Same-second is not before. Use `timeline`, not intuition.
2. **Mechanism.** Can you state the chain in one sentence without the word
   "somehow"? A cache miss spike makes requests slower; it does not exhaust a
   thread pool. If the mechanism needs hand-waving, it is a symptom.
3. **Coverage.** Does the candidate explain *every* error you saw, or only the
   most colourful one? A cause that explains one of three error types is
   incomplete.

## If a candidate fails a test

Go back and search for what would explain the rest. The real cause is usually
quieter: pool saturation, a config value, a limit. Loud metrics are downstream
of quiet limits.

## How to state the answer

Name the real cause first, then explicitly dismiss the decoy and say why:
"the cache miss spike is a consequence of the same saturation, not its cause".
An answer that never mentions the obvious-but-wrong candidate leaves the
reader to wonder whether you saw it.
