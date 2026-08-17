# Test-Guardian Report: variables-provider-global

**Item:** `/variables` uses `get_model(uid)` instead of forcing Kie  
**Date:** 2026-08-17  
**Auditor:** test-guardian  
**Plan:** `.planning/quick/variables-provider-global/PLAN.md`  
**Summary:** `.planning/quick/variables-provider-global/SUMMARY.md`  
**Impact:** `.grok/agent-memory/impact-analyzer/variables-provider-global.md`  
**Arch:** `.grok/agent-memory/arch-enforcer/variables-provider-global.md`  
**Primary tests:** `tests/test_variables_command.py`  
**Commit audited:** `a22ddaea3b3c8e04a88ad347074a0262411ed9f0`

**Verdict:** suite protege adecuadamente

## Coverage Audit

All 10 fail-if DoD items are present, use `_set_user_image_config` (hydrated `user_state`, not disk-only `sessions.set_grok_imagine_config`), and exercise real `get_model` / allowlist / regen builder.

| # | Contract | Test | Status |
|---|----------|------|--------|
| 1 | xAI batch `provider=="xai"` with empty `KIE_API_KEY` | `test_batch_uses_xai_when_configured` | covered |
| 2 | Replicate Grok `provider=="replicate"` | `test_batch_uses_replicate_when_configured` | covered |
| 3 | `grok_video` rejected, no `generate_image` | `test_batch_rejects_grok_video` | covered |
| 4 | `faceswap` rejected, no `generate_image` | `test_batch_rejects_faceswap` | covered |
| 5 | Reply + Kie ref + non-kie downloads Telegram photo | `test_cmd_variables_reply_ignores_kie_ref_when_provider_not_kie` | covered |
| 6 | Regen context provider matches configured provider | `test_batch_regen_context_matches_configured_provider` | covered + tightened |
| 7 | Default-kie path still works | `test_batch_generates_count_images_with_distinct_prompts`, `test_batch_regen_context_has_kie_provider`, `test_batch_no_kie_api_key`, `test_cmd_variables_reply_uses_kie_ref_for_bot_image` | covered + tightened |
| 8 | ComfyUI trio | `test_batch_comfyui_uses_selected_model_and_sends_via_comfyui`, `test_batch_comfyui_requires_host`, `test_batch_comfyui_rejects_video_model` | covered |
| 9 | Original image reused; cancel; stop-on-error; empty-list | `test_batch_reuses_original_image` (seek(0) + same object), `test_batch_cancel_stops_after_completed`, `test_batch_stops_on_provider_error`, `test_batch_empty_list_guard` | covered |
| 10 | `/listas` tests untouched and still pass | `tests/test_variables_flow.py` / `variables_flow.py` / `variables_store.py` not in item diff; included in 196-pass regression | covered |

Also present (PLAN must-add): `test_batch_uses_seedream_when_selected`, `test_batch_xai_missing_key`, `test_batch_replicate_missing_token`.

**Gap found and closed in this pass:** batch tests did not assert A13 both ways (`kie_source_ref` forwarded when provider is kie; stripped before `generate_image` when not). Tightened two existing tests — no new test functions.

Mutation-lite (Fase 5):
- Always pass raw `kie_source_ref` → `test_batch_regen_context_matches_configured_provider` **fails**.
- Always `kie_ref = None` → `test_batch_regen_context_has_kie_provider` **fails**.
- Production line restored; `bot.py` dirty-diff empty.

## Mock Audit

| Archivo | Mock / patch | Clasificación | Path de negocio | Acción |
|---------|--------------|---------------|-----------------|--------|
| `tests/test_variables_command.py` | `generate_image` | PERMITIDO | provider I/O (xAI/Kie/Replicate/ComfyUI) | ninguna |
| same | `process_image_result` | PERMITIDO | Telegram send + download | ninguna |
| same | `_send_comfyui_output` | PERMITIDO | ComfyUI delivery I/O | ninguna |
| same | `_download_telegram_photo` / `_download_telegram_file_id` | PERMITIDO | Telegram file I/O | ninguna |
| same | `_resolve_reply_kie_ref` | PERMITIDO | reply-ref lookup edge | ninguna |
| same | `_run_variables_batch` in reply/entry tests | PERMITIDO | isolates `cmd_variables_reply` gate; batch contracts tested without this mock | ninguna |
| same | `variables_store.random_combination` | PERMITIDO | determinism (PLAN-allowed) | ninguna |
| same | `variables_store.get_lists` (empty-list test) | PERMITIDO | existing empty-list isolation; store is no-touch | ninguna |
| same | `_job_cancelled` | PERMITIDO | simulate cancel mid-loop | ninguna |
| same | `monkeypatch` `KIE_API_KEY` / `XAI_API_KEY` / `REPLICATE_TOKEN` / `COMFYUI_HOST` | PERMITIDO | credential preflight | ninguna |
| same | `_comfyui_is_video` in `test_batch_comfyui_rejects_video_model` | PERMITIDO (excepción PLAN) | existing ComfyUI video test | ninguna — flagged, not rewritten |
| same | `MagicMock` Message / status / `bot.session` | PERMITIDO | Telegram I/O / dispatcher | ninguna |
| same | `cmd_variables_photo` / `cmd_variables_reply` / `_process_single_photo_edit` in routing tests | PERMITIDO | dispatcher routing, not item core | ninguna |
| — | `get_model` | **not mocked** | routing source of truth | — |
| — | `_download_allowlist_for_provider` | **not mocked** | SSRF allowlist | — |
| — | `_build_image_regen_context` | **not mocked** | regen provider / kie_ref keys | — |
| — | `_variables_model_or_reject` | **not mocked** | reject order | — |

**Resumen mocks:** 13 permitidos en scope del ítem, **0 prohibidos**.  
**Confianza de realidad:** alta — handler/batch → `get_model` real + allowlist real + regen builder real; mocks only at Telegram / `generate_image` / result senders.

No disk-only `sessions.set_grok_imagine_config` after hydration. Provider tests mutate hydrated state via `_set_user_image_config`. Existing ComfyUI tests still replace `user_state[1001] = {"model": "comfyui"}` (PLAN-allowed as-is).

## Re-run Results

```
./venv/bin/python -m pytest tests/test_variables_command.py -q
→ 41 passed (0.19s)

./venv/bin/python -m pytest tests/test_variables_command.py tests/test_variables_flow.py tests/test_variables_store.py tests/test_album_batch.py tests/test_cancel_job.py tests/test_kie_provider.py -q
→ 196 passed (0.70s)
```

Warnings are pytest-asyncio `get_event_loop_policy` deprecations on Python 3.14 — pre-existing, not attributable.

## Pre-existing vs Attributable

- 0 failures on primary or regression.
- pytest-asyncio DeprecationWarning flood: pre-existing on 3.14.
- `_run_variables_batch` still >50 LOC: residual from arch-enforcer / PLAN A11 (no service extract).
- `_comfyui_is_video` mock: pre-existing ComfyUI trio; PLAN explicitly allows it.
- Full `tests/` not re-run this pass (executor SUMMARY already 375 passed / 2 skipped live smoke). Guardian ran the two command tiers requested.

## Residuals (out of DoD — do not inflate)

- `_run_variables_batch` size (telegram-bot-hardener >50 LOC). Follow-up only.
- No dedicated Replicate sibling for regen context (xAI covers A13).
- Reject-order video → faceswap → credentials not independently sequenced beyond video+empty-KIE and faceswap (faceswap provider is `replicate`, so credentials-first would not swap the Face Swap message).
- `delete_status=False` on `process_image_result` now asserted on both regen tests; ComfyUI trio already had it.

## Tests added / changed

No new test functions. Strengthened:

- `test_batch_regen_context_has_kie_provider` — pass real `kie_ref`; assert forwarded to `generate_image` + regen; assert `download_allowlist=="kie"` and `delete_status=False`.
- `test_batch_regen_context_matches_configured_provider` — assert `generate_image(..., kie_source_ref=None)`; assert `download_allowlist=="xai"` and `delete_status=False`.

Do not commit from this role (orchestrator / commit gate).

## Handoff

Listo para cierre (paso 6 — tests finales / commit gate).  
`next_recommended`: close / final test run.

Gate: veredicto positivo + tests del ítem actualizados + Mock Audit **0 prohibidos** en scope.
