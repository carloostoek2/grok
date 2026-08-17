# Pool Documentation: variables-provider-global

**Items:** 1
**Date:** 2026-08-17
**Mode:** Feature GSD-lite (quick plan) — not `--hardening`
**Plan:** `.planning/quick/variables-provider-global/PLAN.md`
**Summary:** `.planning/quick/variables-provider-global/SUMMARY.md`

## Consolidated Outcomes

### Item: Make `/variables` use the user's configured image model/provider

`/variables` batch image edits now use `get_model(uid)` — the same source as regular photo edits — instead of rewriting every non-ComfyUI user onto Kie Grok Imagine.

| Area | Before | After |
|------|--------|-------|
| Model source | else-branch `_grok_model_for_config(uid, "kie", variant)` | `get_model(uid)` via `_variables_model_or_reject` |
| Allowed backends | ComfyUI image + forced Kie | grok (xAI / Replicate / Kie), Seedream, ComfyUI image |
| Video / faceswap | `grok_video` remapped to Kie image | rejected before `_start_job` (video → faceswap → credentials → empty lists) |
| Reply `kie_source_ref` | always preferred | only when active `provider=="kie"`; else download Telegram photo + `source_file_id` |
| Download allowlist | hardcoded `"kie"` | `_download_allowlist_for_provider(model.get("provider"))` |
| README `/variables` | claimed Kie-only | configured image model/provider; video + Face Swap reject documented |

Default remains `model=grok` + `grok_imagine_provider=kie`. Sequential batch, original-image reuse + `seek(0)`, `delete_status=False`, cancel / stop-on-error unchanged.

**Sources:** PLAN.md; executor SUMMARY; impact-analyzer; arch-enforcer (commit `a22ddae`).

### Commits (item)

| Hash | Message |
|------|---------|
| `a22ddaea3b3c8e04a88ad347074a0262411ed9f0` | `feat(variables): run batch on configured image provider` — `bot.py`, `tests/test_variables_command.py`, `README.md` |
| `c7c66405ba57740de14106896b6c61957b93f2ea` | `test(variables): lock i2i image_data and kie reply batch path` — tests only |

### Verifications

| Gate | Result | Source |
|------|--------|--------|
| TDD RED | 10 failed, 17 passed on Kie-forcing / seek / reply-ref | `gsd-variables-provider-global.log` |
| Primary after impl | 41 passed (`test_variables_command.py`) | executor SUMMARY |
| Regression | 196 passed (variables + album + cancel + kie) | executor + test-guardian |
| Full suite | **375 passed**, 2 skipped (live smoke, no `LIVE_SMOKE=1`) | executor SUMMARY |
| After review fix | **43 passed** (`test_variables_command.py`) | executor log + orchestrator close |
| Arch | **PASS WITH NOTES**, 0 critical | arch-enforcer |
| Test-guardian | **suite protege adecuadamente**; 0 mocks prohibidos | test-guardian |
| Review | Effort **3**, **2 rounds**, **0 open issues** | review report |
| Self-check | PASSED (executor + review-fix) | `gsd-variables-provider-global.log` |

README `/variables` already updated in `a22ddae`. No production code in this documentador pass.

## Review stats

| Field | Value | Source |
|-------|-------|--------|
| Effort | 3 (1 general + tests + plan alignment) | `.grok/agent-memory/review/variables-provider-global.md` |
| Rounds | 2 | same |
| Exit | 0 open issues (round 2) | same |
| Bugs | 0 | same |
| Suggestions | 3, all **[Tests]**, all fixed in `c7c6640` | same |
| Nits | 0 | same |

Round 1 [Tests] suggestions (fixed):

1. Non-Kie batches pass original `image_data` BytesIO into `generate_image`.
2. Kie reply batch path with `image_data=None` stays locked.
3. ComfyUI `wan_i2v` reject uses the real detector.

Round 2: [General] 0, [Tests] 0, [Plan] 0.

Test-guardian also tightened two existing regen tests (A13 both ways) without adding functions.

## Learnings / Patterns

- **Routing alignment, not a new backend.** `generate_image` already dispatches `xai` / `kie` / `comfyui` / else-Replicate. The bug was `_run_variables_batch` rewriting non-ComfyUI users onto Kie. Copy `_process_single_photo_edit` + `handle_reply_edit`; do not redesign dispatch.
- **`get_model(uid)` is the only registry.** Do not add a second provider map. Optional helper `_variables_model_or_reject` is reject-order + credential preflight only (PLAN A11 — no service extract).
- **Reject order is load-bearing.** After `get_model`: **video → faceswap → missing credentials → empty lists → `_start_job`**. Video users used to get an accidental Kie *image* batch; after the change they must be rejected or they hit video slugs through the image path.
- **`kie_source_ref` is kie-only (defense in depth).** Reply gate *and* in-loop `kie_ref = kie_source_ref if provider=="kie" else None`. Passing a Kie ref + `image_data=None` to xAI/Replicate silently becomes txt2img and leaves regen without `source_file_id`.
- **Allowlist follows provider.** xAI → `"xai"` (`*.x.ai` / `*.xai.com`); Kie → `"kie"`; Replicate → `None`. Hardcoded `"kie"` fails xAI downloads.
- **Seedream ALLOW, faceswap REJECT.** Seedream is prompt i2i via Replicate `image_input`. Faceswap is a different pipeline (`_handle_faceswap_photo`); do not invent a faceswap-variables path.
- **Test-state gotcha.** Mutate hydrated `user_state` via `_set_user_image_config`. `sessions.set_grok_imagine_config` alone does **not** override an already-hydrated state. Do not replace the entire `user_state[uid]` dict for grok-provider tests (drops imagine fields).
- **Mock policy.** Mock Telegram I/O + `generate_image` / result senders only. Never mock `get_model`, `_download_allowlist_for_provider`, `_build_image_regen_context`, or `_variables_model_or_reject`.
- **BytesIO reuse.** Same original `image_data` every iteration; `seek(0)` at the start of each loop. Replicate may consume the buffer.
- **A13 must be tested both ways.** Always-forward `kie_source_ref` fails the xAI regen test; always-`None` fails the default-kie regen test (test-guardian mutation-lite).

## Residuals

### Auto-items / Deferred

None created. No deferred queue entries for this pool.

### Out of scope (documentados only)

| Residual | Class | Why | Source |
|----------|-------|-----|--------|
| `_run_variables_batch` still >50 LOC (arch: 142 LOC, `bot.py` L1913–2054) | out-of-scope | PLAN A11 forbids a service extract; helper kept the function from growing. telegram-bot-hardener flag is NOTES only. | executor, arch-enforcer, review, test-guardian, orchestrator close |

Test-guardian “do not inflate” notes (not items): no dedicated Replicate sibling for regen context (xAI covers A13); reject-order not independently sequenced beyond video+empty-KIE and faceswap.

Persisted: `.grok/agent-memory/residuals/variables-provider-global.md`

## Roadmap Updates

None. No `HARDENING_ROADMAP.md` / `ROADMAP.md` / `decisions.md` in this repo. Feature close lives in SUMMARY + this file. README `/variables` was already updated in `a22ddae`.

## Docs commit

`docs(variables): record provider-global routing` — 13 documentation paths (PLAN/SUMMARY, gsd logs, agent-memory reports, residuals). Hash reported in the documentador close return.

## Next Steps

- Pool closed. No follow-up item required.
- Residual `_run_variables_batch` size stays documented-only unless a later slice explicitly extracts orchestration (still A11-forbidden for this feature).
- Do not reopen Kie-forcing, video/faceswap rejects, or reply-ref gating without new product scope.
