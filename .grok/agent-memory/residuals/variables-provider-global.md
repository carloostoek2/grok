# Residuals: variables-provider-global

**Pool:** variables-provider-global  
**Date:** 2026-08-17  
**Item:** `/variables` uses configured image model/provider  
**Classification pass:** documentador (pool close)

No auto-created items. No deferred-queue entries.

## Out of scope (documentados only)

### `_run_variables_batch` still exceeds 50 LOC

| Field | Value |
|-------|-------|
| **Class** | out-of-scope |
| **Severity** | observation / NOTES (not a gate failure) |
| **Files** | `bot.py` (`_run_variables_batch`; arch-enforcer L1913–2054, 142 LOC) |
| **Why not this pool** | PLAN A11: extract `_variables_model_or_reject` only; do **not** extract a service layer. telegram-bot-hardener flags functions >50 lines; the helper kept the batch from growing. |
| **Do not** | Reopen this item to split `bot.py` orchestration. |
| **Follow-up only if** | A later, explicit slice owns service extraction (out of current A11). |

**Sources:**

- `.planning/quick/variables-provider-global/PLAN.md` A11
- `.planning/quick/variables-provider-global/SUMMARY.md` Residuals
- `.grok/agent-memory/arch-enforcer/variables-provider-global.md` (observation; 0 critical)
- `.grok/agent-memory/review/variables-provider-global.md` Residuals
- `.grok/agent-memory/test-guardian/variables-provider-global.md` Residuals
- Orchestrator close note

## Not items (guardian notes, do not inflate)

Recorded so a later pool does not treat them as forgotten DoD:

- No dedicated Replicate sibling for regen-context A13 (xAI test covers strip). Source: test-guardian.
- Reject-order video → faceswap → credentials is not independently sequenced beyond `test_batch_rejects_grok_video` (with empty Kie key) and `test_batch_rejects_faceswap`. Faceswap provider is `replicate`, so a credentials-first bug would not swap the Face Swap message. Source: test-guardian.

## Auto-items / Deferred

None.
