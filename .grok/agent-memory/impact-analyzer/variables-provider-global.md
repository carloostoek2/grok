# Impact Analysis: Make /variables provider-global

**Date:** 2026-08-17
**Change:** `/variables` image-edit batches must use `get_model(uid)` (the user's configured model/provider) instead of hardcoding Kie Grok Imagine, except video models which stay rejected.
**Analysis only** — no implementación
**Scope validity:** Tight and implementable as one slice. Do not expand into `/listas`, `variables_store`, combo generation, album batch, or video generation.

## Executive Summary

`/variables` already supports ComfyUI via a special-case branch in `_run_variables_batch`. Every other configured model is rewritten to Kie: the else branch requires `KIE_API_KEY`, builds `model = _grok_model_for_config(uid, "kie", variant)`, and downloads results with `_download_allowlist_for_provider("kie")`. `cmd_variables_reply` always prefers `_resolve_reply_kie_ref` over the Telegram photo, even when the user is on xAI or Replicate.

Regular photo edits (`_process_single_photo_edit`, `handle_reply_edit`, `_process_album_edit_from_file_ids`) already do the right thing: `model = get_model(uid)` → `generate_image(...)` → ComfyUI via `_send_comfyui_output` or `process_image_result(..., download_allowlist=_download_allowlist_for_provider(model.get("provider")))`. `generate_image` already dispatches `xai` / `kie` / `comfyui` / else-Replicate. This change is a routing alignment, not a new generation backend.

Global risk is **medium**. The dangerous part is *not* xAI/Replicate Grok Imagine (those already work as i2i). It is silently sending `get_model()` output for `grok_video` or `faceswap` into `generate_image`. Today `grok_video` users accidentally get a Kie *image* batch because the else branch remaps them. After this change they must be rejected (assumption 2) or they would hit video slugs through the image path. `faceswap` is a separate Replicate swap pipeline (`_handle_faceswap_photo`); it cannot do prompt-based edits. Sensitive systems: `generate_image` / `process_image_result`, Kie `kie_source_ref` + `generation_refs`, SSRF download allowlists, per-user `_active_jobs`, ComfyUI local-file delivery, and the `/listas` FSM (must not be touched).

Default user config remains `model=grok` + `grok_imagine_provider=kie` (`sessions.DEFAULT_*`). Existing Kie-default tests keep passing if we stop *forcing* Kie but still honor the default. New coverage is required for xAI, Replicate, seedream, video reject, faceswap reject, credential preflight, and reply-path `kie_source_ref` gating.

## Current coupling (verified)

```1878:2013:bot.py
async def _run_variables_batch(...):
    use_comfyui = get_user_state(uid)["model"] == "comfyui"
    if use_comfyui:
        model = get_model(uid)
        # COMFYUI_HOST + video reject
    else:
        if not KIE_API_KEY:
            await message.answer(_KIE_NOT_CONFIGURED_MSG)
            return
        variant = get_grok_imagine_config(uid)["variant"]
        model = _grok_model_for_config(uid, "kie", variant)
    ...
    generate_image(..., kie_source_ref=kie_source_ref)
    ...
    process_image_result(..., download_allowlist=_download_allowlist_for_provider("kie"), ...)
```

```2055:2082:bot.py
# cmd_variables_reply always prefers Kie task ref
kie_source_ref = _resolve_reply_kie_ref(message.reply_to_message)
if kie_source_ref is None:
    image_data = await _download_telegram_photo(...)
```

Contrast with the already-correct reply-edit guard:

```2220:2225:bot.py
kie_source_ref = None
image_data = None
if model.get("provider") == "kie":
    kie_source_ref = _resolve_reply_kie_ref(message.reply_to_message)
if kie_source_ref is None:
    image_data = await _download_telegram_photo(...)
```

## Assumptions (planner must document, not re-open)

1. Source of truth is `get_model(uid)` — same as regular photo edits.
2. Video models (`grok_video`, ComfyUI `wan_i2v`) are rejected with a message to pick an image model in `/config`.
3. `kie_source_ref` is used only when the *active* provider is `kie`; otherwise always download the Telegram photo and set `source_file_id` (regen depends on it).
4. Credential preflight matches the selected provider. Runtime-optional keys are `KIE_API_KEY` and `COMFYUI_HOST`. `XAI_API_KEY` and `REPLICATE_API_TOKEN` are required at process import (`os.environ["..."]` in `bot.py` L42–43); empty-string guards are for monkeypatched tests / defense in depth, not production missing-env.
5. **Seedream: allow.** `_generate_replicate` already maps `image_data` → `image_input` for `key=="seedream"`. **Faceswap: reject** with a clear user message. Face-swap is not prompt-based i2i (`cdingram/face-swap` expects source/target faces via `_handle_faceswap_photo`, not `prompt`+`image`).
6. Do **not** change `/listas`, `variables_flow.py`, `variables_store.py`, combo generation, cancel/stop-on-error, or original-image reuse.
7. Keep batch sequential + status reuse (`delete_status=False`).
8. Update tests that assert Kie-forcing. Add xAI + Replicate (+ seedream / video / faceswap) coverage.
9. Update README `/variables` section.

## Consumers / Call Sites Map

### Batch orchestration (edit)

| Site | File:line | Role |
|------|-----------|------|
| `_run_variables_batch` | `bot.py:1878-2032` | **Primary edit.** Forces Kie except ComfyUI. |
| `cmd_variables_photo` | `bot.py:2035-2052` | Photo+caption entry. Always downloads Telegram file. No provider logic. Keep. |
| `cmd_variables_reply` | `bot.py:2055-2082` | **Edit.** Always prefers Kie ref; must gate on `get_model(uid)["provider"]=="kie"`. |
| `cmd_variables_help` | `bot.py:1851-1875` | Delegates photo/reply; usage text is provider-agnostic. Optional comment-only. |
| `_is_variables_command` / `_parse_variables_count` | `bot.py:1822-1848` | Parsing only. No touch. |

### Routing into the batch (no-touch unless comments)

| Site | File:line | Role |
|------|-----------|------|
| `handle_photo_caption` | `bot.py:2093-2096` | Caption `/variables N` → `cmd_variables_photo`. |
| `handle_reply_edit` | `bot.py:2190-2193` | Reply `/variables N` → `cmd_variables_reply`. |
| Dispatcher `Command("variables")` | `bot.py:1851` | Registered before photo/reply handlers; already delegates. |

### Pattern to copy (no-touch)

| Site | File:line | What to copy |
|------|-----------|--------------|
| `_process_single_photo_edit` | `bot.py:1540-1645` | `get_model` → `generate_image` → ComfyUI vs `process_image_result` + provider allowlist. |
| `_process_album_edit_from_file_ids` | `bot.py:1648-1767` | Sequential batch + `delete_status=False` + provider allowlist. |
| `handle_reply_edit` Kie guard | `bot.py:2220-2225` | `kie_source_ref` only when `provider=="kie"`. |
| `generate_image` | `bot.py:2981-3020` | Dispatch xai / kie / comfyui / replicate. Do not change. |
| `_build_image_regen_context` | `bot.py:673-703` | Already records `model.provider` + grok imagine fields. |

### Downstream consumers of batch outputs (must keep working)

| Site | File:line | Why it matters |
|------|-----------|----------------|
| `process_image_result` | `bot.py:3816-3867` | Uses `download_allowlist` + `kie_meta` + `regen_context`. Wrong allowlist = SSRF miss or download fail. |
| `_send_comfyui_output` | `bot.py:3194+` | ComfyUI path; already used by variables. Keep `delete_status=False`. |
| `handle_regenerate_image` | `bot.py:1154-1262` | Rebuilds model via `_model_from_regen`. If reply used Kie ref without `source_file_id` on a non-Kie batch, regen cannot recover the original. |
| `sessions.save_generation_ref` | `sessions.py:447` | Stores provider + `kie_task_id` + regen. |
| `_resolve_reply_kie_ref` | `bot.py:706-716` | Only returns a ref when stored `provider=="kie"`. |
| `_start_job` / `_finish_job` | `bot.py:432-460` | One job per user, kind `"variables"`. Do not change. |
| `generate_image` → `_generate_xai` | `bot.py:3357-3413` | i2i via `/images/edits`; seeks BytesIO. |
| `generate_image` → `_generate_kie` | `bot.py:3702+` | Uses `kie_source_ref` or upload; own `KIE_API_KEY` check. |
| `generate_image` → `_generate_replicate` | `bot.py:3023-3040` | Seedream `image_input`; grok Replicate `image`. Faceswap schema mismatch. |
| `variables_store.random_combination` | `variables_store.py` | Combo/prompt. No touch. |
| `variables_flow` `/listas` | `variables_flow.py` | Admin panel. No touch. |

### Tests that assert current Kie forcing

| Test | File:line | Today |
|------|-----------|-------|
| `test_batch_generates_count_images_with_distinct_prompts` | `tests/test_variables_command.py:408-437` | `assert call.args[0]["provider"] == "kie"` |
| `test_batch_forces_kie_and_reuses_original_image` | `tests/test_variables_command.py:440-460` | Name implies force; body only checks image reuse (keep). |
| `test_batch_no_kie_api_key` | `tests/test_variables_command.py:526-532` | Empty `KIE_API_KEY` always rejects (default model is still kie — keep, add companions). |
| `test_batch_regen_context_has_kie_provider` | `tests/test_variables_command.py:535-559` | `regen["provider"]=="kie"` |
| `test_cmd_variables_reply_uses_kie_ref_for_bot_image` | `tests/test_variables_command.py:181-189` | Always passes Kie ref; valid only when active provider is kie (default). |

### Docs

| Site | File:line | Today |
|------|-----------|-------|
| README `/variables` | `README.md:30-37` | "**Kie.ai** provider" |
| Section comment | `bot.py:1819-1820` | "batch image editing ... (Kie)" |
| `_run_variables_batch` docstring | `bot.py:1886` | "Run `count` Kie image edits" |

## Risks

### Critical

1. **`grok_video` silently becomes a broken image call.**
   Today the else branch remaps video users onto Kie Grok Imagine image. After `get_model(uid)`, `generate_image` would receive `key=grok_video` and `id=grok-imagine-video` (or Kie video slugs) and hit `_generate_xai` / `_generate_kie` image APIs.
   **Mitigation:** Reject `model["key"]=="grok_video"` and ComfyUI video (`_comfyui_is_video`) *before* `_start_job`, same user message as today's ComfyUI video reject (`bot.py:1902-1906`). Add a unit test. This is an intentional UX change for video-configured users — document it.

2. **`faceswap` routed through `generate_image`.**
   Face-swap is not i2i. `_generate_replicate` would send `{prompt, image}` to `cdingram/face-swap`, which expects swap/target images.
   **Mitigation:** Reject with a message to pick an image model in `/config`. Do not invent a faceswap variables path.

3. **Reply `kie_source_ref` on a non-Kie provider.**
   If `cmd_variables_reply` still prefers the Kie ref while `get_model` is xAI/Replicate, `_generate_xai`/`_generate_replicate` ignore `kie_source_ref` and receive `image_data=None` → txt2img instead of edit, and regen has no `source_file_id`.
   **Mitigation:** Copy `handle_reply_edit` L2220-2225. When not kie: download photo, set `source_file_id`, pass `kie_source_ref=None`.

### Medium

4. **SSRF / download allowlist hardcoded to `"kie"`.**
   xAI results live on `*.x.ai` / `*.xai.com`. Passing `"kie"` into `process_image_result` will fail xAI downloads (`_is_host_allowed_for_download`). Replicate needs `None` (no host check).
   **Mitigation:** `_download_allowlist_for_provider(model.get("provider"))` exactly like `_process_single_photo_edit` L1617.

5. **Credential preflight still Kie-only.**
   An xAI user with empty `KIE_API_KEY` is blocked today (`test_batch_no_kie_api_key`). After the change that test stays valid only for kie-configured users. Missing Kie key must not block xAI/Replicate/seedream/ComfyUI.
   **Mitigation:** Preflight by `model["provider"]` / `model["key"]`:
   - `kie` → `KIE_API_KEY` else `_KIE_NOT_CONFIGURED_MSG`
   - `comfyui` → `COMFYUI_HOST` (already)
   - `xai` → optional empty `XAI_API_KEY` guard for tests
   - `replicate` (grok/seedream) → optional empty `REPLICATE_TOKEN` guard
   - Keep preflight *before* empty-list check and `_start_job` (current UX).

6. **Regen context / original-image recovery.**
   `_build_image_regen_context` already writes the real provider once `model` is correct. Must not pass `kie_source_ref` into regen when provider is not kie. Non-kie batches must always have `source_file_id`.
   **Mitigation:** Tests: default kie still has `provider=="kie"`; xAI/Replicate regen has matching provider + `source_file_id` and no `kie_source_ref`.

7. **BytesIO cursor reuse across providers.**
   The same `image_data` object is passed every iteration (`test_batch_forces_kie_and_reuses_original_image`). `_image_to_data_uri` and `_generate_kie` seek(0); Replicate `replicate.run` may consume the buffer.
   **Mitigation:** `image_data.seek(0)` at the start of each loop iteration when `image_data` is not None. Cheap, keeps original-reuse invariant.

### Low

8. **Default provider is still kie.** Existing tests that assume kie without setting state keep passing. Easy to ship without xAI/Replicate coverage.
   **Mitigation:** New tests must *set* `user_state` provider (see test-guardian). Disk `sessions.set_grok_imagine_config` alone does **not** update an already-hydrated `user_state`.

9. **`_run_variables_batch` size.** Already ~155 lines (telegram-bot-hardener flag: functions >50). Do not extract a service layer in this slice. A tiny `_variables_model_or_reject(uid) -> tuple[dict,str|None]` helper is acceptable if it keeps the function from growing.

10. **Help text / privacy.** `cmd_variables_help` is already provider-agnostic. README is not. Kie privacy notice in README env/video sections stays; `/variables` must not claim Kie-only.

11. **Concurrency.** `_start_job` already cancels any previous job for the user. Sequential loop + cancel checks stay. No new race if we do not parallelize.

## Affected Tests

### Must update

`tests/test_variables_command.py`

- `test_batch_generates_count_images_with_distinct_prompts` (L408): keep default-kie `provider=="kie"` **or** rename comment from "forced" to "default config is kie".
- `test_batch_forces_kie_and_reuses_original_image` (L440): **rename** to `test_batch_reuses_original_image`; keep reuse assertion; do not require force-to-kie.
- `test_batch_no_kie_api_key` (L526): keep as kie-default / explicit-kie reject.
- `test_batch_regen_context_has_kie_provider` (L535): keep for default kie; add sibling tests for other providers.
- `test_cmd_variables_reply_uses_kie_ref_for_bot_image` (L181): keep for default kie; add non-kie companion.

### Must add

| Test | Asserts |
|------|---------|
| `test_batch_uses_xai_when_configured` | `user_state` provider `xai` → `generate_image` model `provider=="xai"`, allowlist `"xai"`, works with `KIE_API_KEY=""` |
| `test_batch_uses_replicate_when_configured` | provider `replicate` → model `provider=="replicate"`, allowlist `None` |
| `test_batch_uses_seedream_when_selected` | `model=="seedream"` → `key=="seedream"`, `provider=="replicate"` |
| `test_batch_rejects_grok_video` | `model=="grok_video"` → user message contains "video", no `generate_image` |
| `test_batch_rejects_faceswap` | `model=="faceswap"` → reject, no `generate_image` |
| `test_cmd_variables_reply_ignores_kie_ref_when_provider_not_kie` | xAI (or Replicate) + mocked kie ref → downloads Telegram photo, batch gets `image_data` and `kie_source_ref is None`, `source_file_id` set |
| `test_batch_regen_context_matches_configured_provider` | xAI/Replicate regen `provider` + `imagine_provider` match; no `kie_source_ref` |
| `test_batch_xai_missing_key_if_guard_added` | only if planner adds empty `XAI_API_KEY` preflight |

### Must still pass (no intended edits)

- Rest of `tests/test_variables_command.py` (routing, dispatcher, cancel, empty lists, ComfyUI trio)
- `tests/test_variables_flow.py` — `/listas` FSM
- `tests/test_variables_store.py` — lists/template/combos
- `tests/test_album_batch.py` — `generate_image` contract / `kie_source_ref` not used for album inputs
- `tests/test_cancel_job.py` — `_start_job` / cancel
- `tests/test_kie_provider.py::test_default_grok_imagine_provider_is_kie`

### Exact commands

Primary:

```bash
./venv/bin/python -m pytest tests/test_variables_command.py -q
```

Focused new/changed:

```bash
./venv/bin/python -m pytest tests/test_variables_command.py -q -k "batch or cmd_variables_reply or kie_ref"
```

Regression around shared image/job paths:

```bash
./venv/bin/python -m pytest \
  tests/test_variables_command.py \
  tests/test_variables_flow.py \
  tests/test_variables_store.py \
  tests/test_album_batch.py \
  tests/test_cancel_job.py \
  tests/test_kie_provider.py \
  -q
```

Full suite (README / CI):

```bash
./venv/bin/python -m pytest tests/ -q
```

`pytest.ini`: `asyncio_mode = auto`, `testpaths = tests`. Use `./venv/bin/python -m pytest` (repo convention). Do not run `tests/test_live_smoke.py` against real APIs unless explicitly requested (`LIVE_SMOKE=1`).

## Files Map

### Edit

- `bot.py`
  - `_run_variables_batch` (L1878-2032): `get_model(uid)`; reject video + faceswap; provider credential preflight; `download_allowlist` from model; seek(0) per iteration; docstring/comment.
  - `cmd_variables_reply` (L2055-2082): Kie-ref only when `get_model(uid).provider == "kie"`.
  - Section comment L1819-1820 (Kie → configured image model).
- `tests/test_variables_command.py` — retarget Kie-forcing tests; add xAI/Replicate/seedream/video/faceswap/reply-ref tests.
- `README.md` L30-37 — `/variables` uses the configured image model/provider, not Kie-only.

### Create

- None required.

### No touch

- `variables_flow.py` — `/listas` admin panel
- `variables_store.py` — lists, template, `random_combination`
- `sessions.py` — persistence, `save_generation_ref`, defaults
- `config_flow.py` — `/config` FSM
- Video generation (`generate_video`, `_do_generate_video`, `_generate_xai` video, Kie video slugs)
- `generate_image` / `_generate_xai` / `_generate_kie` / `_generate_replicate` internals
- `_process_single_photo_edit` / `_process_album_edit_from_file_ids` (copy from, do not change)
- `process_image_result` internals
- `src/*`, `download.py`

## Telegram-bot-hardener notes (aiogram 3)

- Framework: **aiogram 3** (`from aiogram import ...`). Not PTB. Channel-admin / gamification / narrative systems from the skill do **not** exist in this repo.
- Coupling smell: `_run_variables_batch` hardcodes provider instead of using `get_model` (same dict every other image path uses). Fix the coupling; do not introduce a service layer in this slice.
- `_run_variables_batch` is already >50 lines; keep the change mechanical.
- Error/cancel path is already solid: stop on first provider error, `_cancel_job_keyboard`, one job per user.
- Testability is good: batch is a plain async function with patched `generate_image` / `process_image_result`.
- SSRF: never pass user-controlled result URLs without `_download_allowlist_for_provider`.
- Do not let `/variables` text steal `/listas` FSM input (already guarded; do not regress `test_handle_text_defensive_guard_delegates_to_panel`).

## DoD for downstream

### gsd-planner

- Plan only `bot.py` (`_run_variables_batch`, `cmd_variables_reply`, comments), `tests/test_variables_command.py`, `README.md`.
- Document the nine assumptions above as closed. Recommended closed calls: seedream allow, faceswap reject, grok_video reject (breaking vs today's accidental Kie remap).
- Copy `handle_reply_edit` L2220-2225 and `_process_single_photo_edit` L1549-1628. Do not redesign `generate_image`.
- Specify exact pytest commands from this report.
- Call out test-state gotcha: set `bot.get_user_state(uid)["grok_imagine_provider"]` (and `model`); `sessions.set_grok_imagine_config` alone will not override hydrated memory.
- Do not schedule work in `variables_flow.py` / `variables_store.py` / `sessions.py` / `config_flow.py` / video.

### executor

- TDD: change failing Kie-force assertions / add new tests first, then implement.
- After `model = get_model(uid)`, reject in this order: video (`key=="grok_video"` or `_comfyui_is_video`), faceswap, missing provider credentials, empty lists, then `_start_job`.
- `process_image_result(..., download_allowlist=_download_allowlist_for_provider(model.get("provider")), delete_status=False, ...)`.
- ComfyUI path unchanged (`_send_comfyui_output`, `delete_status=False`).
- Sequential loop, original `image_data` reused, `image_data.seek(0)` each iteration, stop on first error, cancel checks stay.
- No production edits outside the files map.

### arch-enforcer

- `/variables` model source == photo-edit source: `get_model(uid)`.
- No second provider registry, no Kie special-case except `kie_source_ref` + Kie credential + Kie allowlist when `provider=="kie"`.
- `kie_source_ref` never leaves the kie provider path.
- Video generation remains unreachable from `/variables`.
- No new persistence. No `/listas` / store / session schema change.
- Download allowlist follows provider (xai/kie/None).

### test-guardian

- Fail the change if any of these are missing:
  1. xAI batch uses `provider=="xai"` and works with empty `KIE_API_KEY`.
  2. Replicate Grok batch uses `provider=="replicate"`.
  3. `grok_video` rejected (no `generate_image`).
  4. `faceswap` rejected (no `generate_image`).
  5. Reply + Kie ref + non-kie provider downloads Telegram photo.
  6. Regen context provider matches configured provider.
  7. Default-kie path still works (backward compatible default).
  8. ComfyUI trio still passes (model, host required, video reject).
  9. Original image reused; cancel; stop-on-error; empty-list guard.
  10. `/listas` tests untouched and still pass.
- Require `user_state` provider setup, not disk-only `sessions.set_grok_imagine_config` after hydration.
- Run the three command tiers above; full `tests/` before handoff.

## Ready for chain

Handoff a gsd-planner con scope tight: `_run_variables_batch` + `cmd_variables_reply` + README + `tests/test_variables_command.py`. Source of truth `get_model(uid)`. Reject `grok_video` / ComfyUI video / faceswap. Allow grok(xai|replicate|kie), seedream, ComfyUI image. Gate `kie_source_ref` on active kie provider. Tests and commands listed above.
