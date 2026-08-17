# Arch Audit: variables-provider-global

**Item:** Make `/variables` use `get_model(uid)` instead of forcing Kie  
**Date:** 2026-08-17  
**Auditor:** arch-enforcer  
**Commit:** `a22ddaea3b3c8e04a88ad347074a0262411ed9f0` (`feat(variables): run batch on configured image provider`)  
**Plan:** `.planning/quick/variables-provider-global/PLAN.md`  
**Summary:** `.planning/quick/variables-provider-global/SUMMARY.md`  
**Impact:** `.grok/agent-memory/impact-analyzer/variables-provider-global.md`

**Verdict:** PASS WITH NOTES  
**Critical violations:** 0

## Findings

### Critical (must fix before advance)

None.

### Medium / Observations

- **Observation — `_run_variables_batch` still 142 LOC** (`bot.py` L1913–2054). telegram-bot-hardener flags functions >50 lines. PLAN A11 forbids a service extract in this slice; the new `_variables_model_or_reject` helper (26 lines) kept the batch from growing. Out-of-scope residual — follow-up only, do not reopen this item.
- **Observation — reply path calls `get_model` twice.** `cmd_variables_reply` (L2093) reads `provider` to gate `kie_source_ref`; `_run_variables_batch` then resolves the model again via the helper. Matches `handle_reply_edit` L2242–2245 and the PLAN Exact implementation. Not a second registry.
- **Observation — project rule files absent.** No `CLAUDE.md` / `AGENTS.md` / `architecture.md` / `rules.md` in this repo. Audit used the PLAN + impact-analyzer contracts and telegram-bot-hardener (aiogram 3; this repo has no channel-admin / gamification / narrative).

## Compliance Checklist

- [x] Capas respetadas — stay in `bot.py` handlers/orchestration; optional `_variables_model_or_reject` only; no service layer
- [x] `/variables` model source == photo-edit source: `get_model(uid)` (helper L1887; photo-edit L1556)
- [x] No `_grok_model_for_config(uid, "kie", ...)` on the `/variables` path
- [x] No second provider registry
- [x] Kie special-case only when `provider=="kie"`: credential preflight, reply `kie_source_ref`, batch `kie_ref` strip, allowlist
- [x] `kie_source_ref` never leaves the kie provider path (reply gate + in-loop `kie_ref`)
- [x] Video generation unreachable from `/variables` (`generate_video` / `_do_generate_video` absent in L1885–2104; video rejected before `_start_job`)
- [x] Faceswap rejected before `generate_image`
- [x] Download allowlist follows provider: `_download_allowlist_for_provider(model.get("provider"))` → xai / kie / None
- [x] No new persistence; no `/listas` / store / session schema change
- [x] Scope del PLAN respetado — commit touches only `bot.py`, `tests/test_variables_command.py`, `README.md`
- [x] No-touch unchanged: `variables_flow.py`, `variables_store.py`, `sessions.py`, `config_flow.py`, `src/*`, `download.py`, `generate_image` internals, video stack, `_process_single_photo_edit`
- [x] Logging adequate — existing `generate_image` print + batch status / stop-on-error / cancel reused
- [x] Tests reflect contracts — real `get_model` + real allowlist; xAI / Replicate / Seedream / video / faceswap / reply-ref / regen / credential / default-kie / ComfyUI trio
- [x] README `/variables` no longer claims Kie-only; Kie privacy paragraph (L28) untouched
- [x] Sequential batch, original-image reuse + `seek(0)`, `delete_status=False`, reject order video → faceswap → credentials → empty lists → `_start_job`

## Contract evidence

| Contract | Evidence |
|----------|----------|
| Same source as photo-edit | `_variables_model_or_reject` → `model = get_model(uid)`. `_process_single_photo_edit` L1556 is the same function. Deleted else-branch that built `_grok_model_for_config(uid, "kie", variant)`. |
| No second registry | Provider still comes from `get_model` / `MODELS`. New symbols are Spanish user-copy constants + one reject helper. |
| `kie_source_ref` kie-only | Reply: resolve only if `get_model(...).get("provider") == "kie"`. Batch: `kie_ref = kie_source_ref if model.get("provider") == "kie" else None` passed to `generate_image` and `_build_image_regen_context`. Test `test_batch_regen_context_matches_configured_provider` injects a Kie ref on xAI and asserts no `kie_source_ref` in regen. |
| Video unreachable | Helper rejects `key=="grok_video"` or `_comfyui_is_video` before empty-list / `_start_job`. Window L1885–2104 has no `generate_video` / `_do_generate_video`. Tests: `test_batch_rejects_grok_video`, `test_batch_comfyui_rejects_video_model`. |
| Allowlist | L2030 uses `_download_allowlist_for_provider(model.get("provider"))`. Tests assert `"xai"`, `None` (Replicate), default kie still `"kie"` via default config. ComfyUI still `_send_comfyui_output`. |
| Scope | `git show --name-status a22ddae` → `README.md`, `bot.py`, `tests/test_variables_command.py` only. HEAD is that commit. |

## telegram-bot-hardener (aiogram 3)

- Framework confirmed: `from aiogram import ...`. Channel-admin / gamification / narrative systems from the skill do not exist here.
- Coupling smell that *was* the bug (hardcoded Kie instead of `get_model`) is fixed. Do not introduce a service layer (A11).
- Error/cancel path unchanged: stop on first provider error, `_cancel_job_keyboard`, one job per user (`kind="variables"`).
- SSRF: result URLs still go through `_download_allowlist_for_provider`.
- Tests mock Telegram I/O + `generate_image` / result senders only; they do **not** mock `get_model`, `_download_allowlist_for_provider`, `_variables_model_or_reject`, or `_build_image_regen_context`.

## Handoff

Advance to **test-guardian**. Gate is 0 critical.

`next_recommended`: test-guardian
