# Residuals: comfyui-refine-confirm

**Pool:** comfyui-refine-confirm (4 items)
**Date:** 2026-08-19
**Items:**
1. Box `REFINE_ONLY` (`comfyui-vast-setup`, commits `384002c` + `1be1d9f`)
2. Bot core 2-stage gen + interactive refine (`bot.py`, commits `2ff0cfd`/`a1c9f0d`/`f6f7f63`)
3. Chains wiring (`bot.py`, commits `378708d`/`71d8beb`/`2afb4f1`)
4. Deferred tests + README (`05c70c0`/`19cd138`)
**Classification pass:** documentador (pool close)

## Handoff (CRITICAL — action required before the flow works in prod)

### Deploy `gen_comfy.py` to the Vast box

| Field | Value |
|-------|-------|
| **Class** | handoff |
| **Severity** | CRITICAL — blocks the feature in production |
| **Action** | Manual operator deploy: `cp /home/ubuntu/comfyui-vast-setup/gen_comfy.py /workspace/gen_comfy.py` |
| **When** | Before the refine-confirmation flow is used in production. The box still runs the OLD `gen_comfy.py` (no `REFINE_ONLY`). |
| **Why** | Refine lives on the box; the bot orchestrates in 2 steps via `REFINE_ONLY` (item 1). Without the deploy, `REFINE_ONLY`/`REFINE_INPUT` are ignored and the flow cannot refine. |
| **Documented in** | grok `README.md` (ComfyUI refine-confirmation section) + `comfyui-vast-setup` `README.md` (refine-only mode section). |
| **Do not** | Treat this as auto-deployed; it is a manual operator action. |

**Sources:** review item 1 + item 4 residuals; arch-enforcer item 1 recommendation; planner item 4 Part C.

## In-scope followup (deferred, documented only)

### `handle_album` does not route comfyui multi-photo albums

| Field | Value |
|-------|-------|
| **Class** | in-scope-followup (deferred) |
| **Severity** | LOW — no user-facing impact today |
| **Files** | `bot.py` (`_process_album_edit_from_file_ids` comfyui branch is defensive/dead today; `handle_album` discards comfyui media groups) |
| **Why not this pool** | Out of item scope; the album comfyui branch was wired defensively and is NOT reachable via `handle_album`. README explicitly does NOT claim multi-photo comfyui albums work. |
| **Do not** | Reopen this item to make `handle_album` route comfyui multi-photo albums without new product scope. |
| **Follow-up only if** | A later, explicit slice owns multi-photo comfyui album routing. |

**Sources:** review item 3 + item 4 residuals; test-guardian item 4 ("rama multi-foto NO ruteada — rama defensiva documentada"); arch-enforcer item 3; README.

## Out of scope (documentados only — committed here as docs)

### Pipeline artifacts of the pool

| Field | Value |
|-------|-------|
| **Class** | out-of-scope |
| **Severity** | none (operational) |
| **Files** | `.grok/agent-memory/*` (arch-enforcer, documentador, gsd-planner, impact-analyzer, residuals, review, test-guardian), `.planning/quick/20260819-comfyui-refine-confirm-itemN/*` (PLAN.md + SUMMARY.md), `gsd-*.log` |
| **Why** | Production/box/tests already committed in the 4 items. These are pipeline artifacts committed by the documentador as docs at pool close. |
| **Do not** | Treat them as runtime data or production code. |

`variables_extract/` (pre-existing untracked) is NOT part of this pool and NOT committed here.

## Not items (guardian notes, do not inflate)

Recorded so a later pool does not treat them as forgotten DoD:

- Item 2 test-guardian GAPS 1-5 (cancel-branch flow, refine-failure notification, album
  no/timeout, choke-negative, trivial) → absorbed by item 4 (a)-(f). Closed.
- Item 3 test-guardian GAPS 1-2 (album chain with real choke, cancel mid-chain) → absorbed
  by item 4 (e)-(f). Closed.
- Item 1 test-guardian LOW residuals (multi-output per base, `true`/`yes` variants, empty CSV
  entries, REFINE_ONLY+krea2 precedence) → outside PLAN Truths/DoD. Optional future tests.
- Item 4 arch NOTES 1-2 ((d) choke mock-spy; (d) reply_markup assert more correct than spec)
  → benign, documented, no action.
- Item 4 test-guardian residual 1 (test (a) leaves uid 6021 job in `_active_jobs`) → inert
  (unique uids), minor hygiene only.
- Item 2 arch improvement: yes-single unwraps `refined[0]` — fixes a latent PLAN bug; keep.
- Item 2 arch out-of-scope: album branch does not check `_send_comfyui_album` base return
  (None → confirm_msg still sent) — probability negligible (freshly downloaded files).
- Item 2 arch out-of-scope: refine-error `status_msg.edit_text(rerr, reply_markup=None)`
  drops the cancel keyboard — harmless (job ends after return); minor inconsistency with the
  re-apply-reply_markup gotcha.

## Auto-items / Deferred queue

No auto-created items. The single `in-scope-followup` above is documented, not queued.
