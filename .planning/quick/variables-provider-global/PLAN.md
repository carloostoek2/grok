---
phase: quick
plan: variables-provider-global
type: auto
item: Make /variables use the user's configured image model/provider
source: user-request
mode: sparse-request
impact_ref: .grok/agent-memory/impact-analyzer/variables-provider-global.md
test_command: ./venv/bin/python -m pytest tests/test_variables_command.py -q
---

## Objective

`/variables` batch image edits must use `get_model(uid)` — the same source of truth as regular photo edits — instead of rewriting every non-ComfyUI user onto Kie Grok Imagine. After this change, a user configured for xAI, Replicate Grok, Seedream, or ComfyUI image gets that backend; video (`grok_video`, ComfyUI `wan_i2v`) and Face Swap are rejected with a message to pick an image model in `/config`. Default config remains `model=grok` + `grok_imagine_provider=kie`, so existing Kie-default tests stay valid without forcing Kie.

This is a routing alignment, not a new generation backend. `generate_image` already dispatches `xai` / `kie` / `comfyui` / else-Replicate.

## Scope

- **In:**
  - `_run_variables_batch` (`bot.py` ~1878–2032): resolve model via `get_model(uid)`; reject video then faceswap then missing credentials; provider-aware download allowlist; `image_data.seek(0)` each iteration.
  - `cmd_variables_reply` (`bot.py` ~2055–2082): `kie_source_ref` only when active `provider=="kie"`.
  - Optional tiny helper `_variables_model_or_reject(uid) -> tuple[dict | None, str | None]` placed immediately above `_run_variables_batch`.
  - Comments/docstring at `bot.py` L1819–1820 and `_run_variables_batch` docstring (drop “Kie-only”).
  - Tests in `tests/test_variables_command.py` (retarget Kie-forcing; add xAI/Replicate/seedream/video/faceswap/reply-ref/regen/credential coverage).
  - README `/variables` section (L30–37): configured image model, not Kie-only.
- **Out / Non-goals:**
  - `/listas`, `variables_flow.py`, `variables_store.py`, combo generation, template.
  - `sessions.py` schema / `save_generation_ref` / defaults.
  - `config_flow.py`.
  - `generate_image` / `_generate_xai` / `_generate_kie` / `_generate_replicate` internals.
  - `_process_single_photo_edit` / `_process_album_edit_from_file_ids` / `handle_reply_edit` (copy from; do not edit).
  - Video stack (`generate_video`, `_do_generate_video`, Kie video slugs).
  - `process_image_result` internals.
  - `src/*`, `download.py`.
  - Service-layer extraction, new provider registry, parallel batch, chaining results.
- **Constraints:**
  - Feature + tests + README only. Sequential batch + `delete_status=False` stay.
  - No new persistence.
  - Do not invent a faceswap-variables path.
  - Do not let `/variables` steal `/listas` FSM input.

## Assumptions

Closed by impact-analyzer — do not reopen:

- **A1:** Source of truth = `get_model(uid)` (same as regular photo edits).
- **A2:** Reject video: `model["key"]=="grok_video"` and ComfyUI video (`_comfyui_is_video`). Message: pick an image model in `/config`. This is an intentional UX change vs today’s accidental remap onto Kie image.
- **A3:** `kie_source_ref` only when the *active* provider is `kie`; otherwise download the Telegram photo and set `source_file_id` (regen depends on it).
- **A4:** Credential preflight by selected provider only. `KIE_API_KEY` / `COMFYUI_HOST` are runtime-optional. `XAI_API_KEY` / `REPLICATE_API_TOKEN` are required at import (`bot.py` L42–43); empty-string guards exist for monkeypatched tests / defense in depth.
- **A5:** Seedream: ALLOW (`_generate_replicate` already maps `image_data` → `image_input`). Faceswap: REJECT (not prompt i2i; `_handle_faceswap_photo` is a different pipeline).
- **A6:** Do not change `/listas`, `variables_store`, combo generation, cancel/stop-on-error, original-image reuse.
- **A7:** Sequential batch + status reuse (`delete_status=False`).
- **A8:** Update tests that assert Kie-forcing; add xAI / Replicate / seedream / video / faceswap / reply-ref coverage.
- **A9:** Update README `/variables` section.

Planner decisions (reversible, locked for this slice):

- **A10:** Add empty-string credential guards for `xai` (`XAI_API_KEY`) and `replicate` (`REPLICATE_TOKEN`) so tests can preflight without hitting live APIs. Messages mirror `_KIE_NOT_CONFIGURED_MSG`.
- **A11:** Extract `_variables_model_or_reject` so `_run_variables_batch` does not grow. Do **not** extract a service layer.
- **A12:** Reject order after `get_model(uid)` is mandatory: **video → faceswap → missing credentials → empty lists → `_start_job`**.
- **A13:** Defense in depth inside the batch: pass `kie_source_ref` into `generate_image` / `_build_image_regen_context` only when `model.get("provider")=="kie"`.
- **A14:** User-facing strings stay Spanish, matching existing bot copy. Code identifiers/comments stay English.

## Architecture Approach

### QUÉ (behavior / contracts)

**Outcome:** `/variables N` (photo caption or reply) runs `N` sequential i2i edits on the user’s configured **image** model.

**Happy path**

1. User has image model selected via `/config` (default: Grok Imagine + Kie).
2. Sends photo with `/variables N` or replies to a photo with `/variables N`.
3. Batch resolves `model = get_model(uid)`, preflights credentials for that provider, then loops `generate_image(model, combo_prompt, original_image, ...)`.
4. Results go through `_send_comfyui_output` when `provider=="comfyui"`, else `process_image_result` with `download_allowlist=_download_allowlist_for_provider(model.get("provider"))`.
5. Each iteration reuses the **original** `image_data` (never chains results). Status message is reused (`delete_status=False`).

**Rejects (before `_start_job`)**

| Condition | User message | Side effect |
|-----------|--------------|-------------|
| `key=="grok_video"` or `_comfyui_is_video(model)` | `"El modelo de video no aplica para /variables; selecciona un modelo de imagen en /config."` | no `generate_image` |
| `key=="faceswap"` | `"Face Swap no aplica para /variables; selecciona un modelo de imagen en /config."` | no `generate_image` |
| `provider=="kie"` and empty `KIE_API_KEY` | `_KIE_NOT_CONFIGURED_MSG` | no job |
| `provider=="comfyui"` and empty `COMFYUI_HOST` | existing ComfyUI host message | no job |
| `provider=="xai"` and empty `XAI_API_KEY` | `_XAI_NOT_CONFIGURED_MSG` | no job |
| `provider=="replicate"` and empty `REPLICATE_TOKEN` | `_REPLICATE_NOT_CONFIGURED_MSG` | no job |
| any `variables_store.LIST_NAMES` list empty | existing empty-list HTML message | no job |

**Reply contract (`cmd_variables_reply`)**

```
kie_source_ref = None
image_data = None
source_file_id = None
if get_model(uid).get("provider") == "kie":
    kie_source_ref = _resolve_reply_kie_ref(message.reply_to_message)
if kie_source_ref is None:
    image_data = await _download_telegram_photo(...)
    source_file_id = reply_photo.file_id
```

Non-Kie reply **must** download the Telegram photo even if a Kie generation ref exists on the replied message. Passing `image_data=None` + a Kie ref to xAI/Replicate would silently become txt2img and leave regen without `source_file_id`.

**Allowed image backends**

| Config | `get_model` key / provider | Allowlist |
|--------|----------------------------|-----------|
| default grok + kie | `grok` / `kie` | `"kie"` |
| grok + xai | `grok` / `xai` | `"xai"` |
| grok + replicate | `grok` / `replicate` | `None` |
| seedream | `seedream` / `replicate` | `None` |
| comfyui image | `comfyui` / `comfyui` | n/a (`_send_comfyui_output`) |

**Truths (must be true at the end)**

1. `_run_variables_batch` never calls `_grok_model_for_config(uid, "kie", ...)`.
2. Default-kie users still generate with `provider=="kie"` (backward compatible default).
3. xAI users generate with `provider=="xai"` even if `KIE_API_KEY==""`.
4. `grok_video` and `faceswap` never reach `generate_image`.
5. Non-kie reply downloads Telegram photo; `kie_source_ref is None`; `source_file_id` is set.
6. `process_image_result` allowlist equals `_download_allowlist_for_provider(model.get("provider"))`.
7. Original `image_data` is reused; cursor is `seek(0)` at the start of every iteration.

### CÓMO (structure / patterns)

- **Layer:** stay in `bot.py` handlers/orchestration. No new module, no service layer, no second model registry.
- **Pattern to copy:**
  - `bot.py:_process_single_photo_edit` L1549–1628 — `model = get_model(uid)` → `generate_image(...)` → ComfyUI via `_send_comfyui_output` **or** `process_image_result(..., download_allowlist=_download_allowlist_for_provider(model.get("provider")))`.
  - `bot.py:handle_reply_edit` L2220–2225 — `kie_source_ref` only when `model.get("provider")=="kie"`.
  - Keep sequential + `delete_status=False` from current `_run_variables_batch` / album batch (L1648–1767).
- **Interfaces / types:** no new public types. Helper signature:

```python
def _variables_model_or_reject(uid: int) -> tuple[dict | None, str | None]:
    """Return (model, None) or (None, user-facing reject message)."""
```

- **Wiring:**
  - `cmd_variables_photo` — unchanged (always downloads Telegram file, `kie_source_ref=None`).
  - `cmd_variables_reply` — gates Kie ref, then `_run_variables_batch`.
  - `_run_variables_batch` — `model, reject = _variables_model_or_reject(uid)`; if reject → `message.answer(reject)` and return; else existing empty-list check, `_start_job`, loop.
  - `generate_image` — **do not edit**; it already routes by `model["provider"]`.

- **File map:**
  - **Edit:** `bot.py`, `tests/test_variables_command.py`, `README.md`
  - **Create:** none
  - **No-touch:** `variables_flow.py`, `variables_store.py`, `sessions.py`, `config_flow.py`, `src/*`, `download.py`, `generate_image` internals, video stack, `_process_single_photo_edit`, `_process_album_edit_from_file_ids`, `handle_reply_edit`

### Exact implementation

#### 1. Constants (near `_KIE_NOT_CONFIGURED_MSG`, `bot.py` L245)

```python
_XAI_NOT_CONFIGURED_MSG = (
    "xAI no está disponible en este momento. Contacta al administrador del bot."
)
_REPLICATE_NOT_CONFIGURED_MSG = (
    "Replicate no está disponible en este momento. Contacta al administrador del bot."
)
```

Reuse existing video / ComfyUI host copy — do not invent new wording for those two.

#### 2. Helper (immediately above `_run_variables_batch`)

```python
def _variables_model_or_reject(uid: int) -> tuple[dict | None, str | None]:
    model = get_model(uid)
    if model.get("key") == "grok_video" or _comfyui_is_video(model):
        return None, (
            "El modelo de video no aplica para /variables; "
            "selecciona un modelo de imagen en /config."
        )
    if model.get("key") == "faceswap":
        return None, (
            "Face Swap no aplica para /variables; "
            "selecciona un modelo de imagen en /config."
        )
    provider = model.get("provider")
    if provider == "kie" and not KIE_API_KEY:
        return None, _KIE_NOT_CONFIGURED_MSG
    if provider == "comfyui" and not COMFYUI_HOST:
        return None, (
            "ComfyUI no configurado: agrega COMFYUI_HOST y COMFYUI_PORT "
            "al .env y reinicia el servicio."
        )
    if provider == "xai" and not XAI_API_KEY:
        return None, _XAI_NOT_CONFIGURED_MSG
    if provider == "replicate" and not REPLICATE_TOKEN:
        return None, _REPLICATE_NOT_CONFIGURED_MSG
    return model, None
```

Reject order is encoded here: video → faceswap → credentials. Caller then checks empty lists, then `_start_job`.

#### 3. Replace the model-resolution block in `_run_variables_batch`

**Delete** the current `use_comfyui = get_user_state(uid)["model"] == "comfyui"` / else-force-Kie block (L1893–1914).

**Replace with:**

```python
uid = message.from_user.id
model, reject_msg = _variables_model_or_reject(uid)
if reject_msg:
    await message.answer(reject_msg)
    return
use_comfyui = model.get("provider") == "comfyui"
```

Then keep the existing empty-list loop, `_start_job`, sequential for-loop, cancel checks, stop-on-error.

Inside the loop, **before** `generate_image`:

```python
if image_data is not None:
    image_data.seek(0)
kie_ref = kie_source_ref if model.get("provider") == "kie" else None
```

Pass `kie_ref` (not the raw argument) to `generate_image` and `_build_image_regen_context`.

Change allowlist from hardcoded `"kie"` to:

```python
download_allowlist=_download_allowlist_for_provider(model.get("provider"))
```

Keep `delete_status=False` on both `_send_comfyui_output` and `process_image_result`.

Update docstring: “Run `count` image edits on the user's configured image model…”

Update section comment L1819–1820: drop “(Kie)”; say “configured image model”.

#### 4. Gate `cmd_variables_reply` (copy `handle_reply_edit` L2220–2225)

Replace the unconditional `_resolve_reply_kie_ref` with:

```python
kie_source_ref = None
image_data = None
source_file_id = None
if get_model(message.from_user.id).get("provider") == "kie":
    kie_source_ref = _resolve_reply_kie_ref(message.reply_to_message)
if kie_source_ref is None:
    image_data = await _download_telegram_photo(message.reply_to_message.photo[-1])
    source_file_id = message.reply_to_message.photo[-1].file_id
await _run_variables_batch(
    message, count, image_data, kie_source_ref, source_file_id=source_file_id,
)
```

Invalid-count and “reply to a photo” guards stay first.

#### 5. README L30–37

Replace the Kie-only sentence. Keep N clamp, original-image reuse, sequential/cancel/stop-on-error, and `/listas` bullets.

Required meaning:

- `/variables` uses the **configured image model/provider** (Grok Imagine via xAI, Replicate, or Kie.ai; Seedream; ComfyUI image).
- Video models (Grok Imagine Video, ComfyUI Wan i2v) and Face Swap are rejected — pick an image model in `/config`.
- Do **not** claim Kie-only. Leave the Kie privacy notice in the env/video sections untouched.

## Context

@bot.py:1540-1645 `_process_single_photo_edit` (pattern)
@bot.py:1878-2082 `_run_variables_batch` + `cmd_variables_reply` (edit)
@bot.py:2220-2225 `handle_reply_edit` Kie-ref guard (pattern)
@bot.py:596-602 `_download_allowlist_for_provider`
@bot.py:770-814 `get_model`
@bot.py:2981-3020 `generate_image` dispatch (no-touch)
@tests/test_variables_command.py existing batch + reply tests
@README.md:30-37 `/variables` section
@`.grok/agent-memory/impact-analyzer/variables-provider-global.md`

**Test-state gotcha:** set `bot.get_user_state(uid)["grok_imagine_provider"]` (and `model`). `sessions.set_grok_imagine_config` alone will **not** override an already-hydrated `user_state`. Do **not** replace the entire `user_state[uid]` dict for grok-provider tests (that drops imagine fields). Use:

```python
def _set_user_image_config(uid=1001, *, model="grok", provider=None):
    state = bot.get_user_state(uid)
    state["model"] = model
    if provider is not None:
        state["grok_imagine_provider"] = provider
    return state
```

Existing ComfyUI tests that do `bot.user_state[1001] = {"model": "comfyui"}` may stay as-is (they already pass).

## Tasks

### Task 1: Write failing tests for configured-provider /variables

**type:** auto
**Objective:** `tests/test_variables_command.py` encodes the new contracts and fails on current Kie-forcing code.
**Files:** `tests/test_variables_command.py`
**Action:**

STRICT TDD. Do **not** edit `bot.py` in this task. Write tests first; confirm they fail for the right reason.

1. Add `_set_user_image_config` helper (see Context). Use it in every new non-default-provider test.
2. **Retarget existing:**
   - `test_batch_generates_count_images_with_distinct_prompts`: keep `provider=="kie"` assertion; change comment from “forced to Kie” to “default config is kie”.
   - Rename `test_batch_forces_kie_and_reuses_original_image` → `test_batch_reuses_original_image`. Keep same-object reuse. Add `assert image_data.tell() == 0` at the start of the fake `generate_image` (fails today because the loop does not `seek(0)` — acceptable RED; if current cursor is already 0 on a fresh BytesIO, the seek assertion may only go RED after a mid-buffer consume; still add it so GREEN impl is forced to seek).
   - Keep `test_batch_no_kie_api_key` as default-kie reject (`Kie.ai` in message, no `generate_image`).
   - Keep `test_batch_regen_context_has_kie_provider` for default kie.
   - Keep `test_cmd_variables_reply_uses_kie_ref_for_bot_image` for default kie.
   - Keep the ComfyUI trio (`test_batch_comfyui_*`) unchanged.
3. **Add (must exist; names may match exactly):**

| Test | Setup | Assert |
|------|-------|--------|
| `test_batch_uses_xai_when_configured` | `_set_user_image_config(provider="xai")`; `monkeypatch.setattr(bot, "KIE_API_KEY", "")` | `generate_image` model `provider=="xai"`; `process_image_result` `download_allowlist=="xai"`; job completes |
| `test_batch_uses_replicate_when_configured` | provider `"replicate"` | model `provider=="replicate"`; allowlist is `None` |
| `test_batch_uses_seedream_when_selected` | `model="seedream"` | `key=="seedream"` and `provider=="replicate"` |
| `test_batch_rejects_grok_video` | `model="grok_video"`; also `KIE_API_KEY=""` | answer contains `"video"` and not `"Kie.ai"`; `generate_image` not awaited |
| `test_batch_rejects_faceswap` | `model="faceswap"` | answer mentions Face Swap / `/config`; `generate_image` not awaited |
| `test_cmd_variables_reply_ignores_kie_ref_when_provider_not_kie` | provider `"xai"`; `_resolve_reply_kie_ref` returns a fake ref | `_download_telegram_photo` awaited; `_run_variables_batch` called with `image_data` set, `kie_source_ref is None`, `source_file_id` set |
| `test_batch_regen_context_matches_configured_provider` | provider `"xai"` (or also replicate) | `regen_context["provider"]` matches; `source_file_id` set; no `kie_source_ref` key |
| `test_batch_xai_missing_key` | provider `"xai"`; `XAI_API_KEY=""` | xAI unavailable message; no `generate_image` |
| `test_batch_replicate_missing_token` | provider `"replicate"`; `REPLICATE_TOKEN=""` | Replicate unavailable message; no `generate_image` |

4. Mock policy (allowed I/O only):
   - **Mock:** `generate_image`, `process_image_result`, `_send_comfyui_output`, `_download_telegram_photo`, `_resolve_reply_kie_ref` (reply tests), `variables_store.random_combination` (determinism — existing pattern).
   - **Do not mock:** `get_model`, `_download_allowlist_for_provider`, `_build_image_regen_context`, `_variables_model_or_reject`, `_comfyui_is_video` (except the existing ComfyUI video test).
5. Run tests and confirm RED: new provider tests fail because the else branch still forces Kie / requires `KIE_API_KEY`; reply non-kie test fails because Kie ref is always preferred; reject tests fail because video is remapped to Kie image.

**Verification:**

```bash
./venv/bin/python -m pytest tests/test_variables_command.py -q -k "batch or cmd_variables_reply or kie_ref"
```

Expect failures on the new/changed cases. Existing default-kie + ComfyUI + routing tests may still pass.

**Done:** All listed tests exist. At least `test_batch_uses_xai_when_configured`, `test_batch_rejects_grok_video`, `test_batch_rejects_faceswap`, and `test_cmd_variables_reply_ignores_kie_ref_when_provider_not_kie` fail on current production code.

### Task 2: Implement get_model routing, rejects, allowlist, reply gate

**type:** auto
**Objective:** Production code matches Task 1 contracts; primary suite is green.
**Files:** `bot.py` (`_KIE_NOT_CONFIGURED_MSG` neighborhood, new helper, `_run_variables_batch`, `cmd_variables_reply`, section comment + docstring)
**Action:**

Implement exactly as specified in Architecture Approach § Exact implementation.

Do **not**:

- Touch no-touch files.
- Call `_grok_model_for_config(uid, "kie", variant)` from `/variables`.
- Extract a service module.
- Parallelize the loop.
- Change cancel / stop-on-error / empty-list / `_start_job` kind `"variables"`.
- Edit `cmd_variables_photo` beyond comments.
- Pass user-controlled result URLs without `_download_allowlist_for_provider`.

After impl, re-run Task 1 command until green.

**Verification:**

```bash
./venv/bin/python -m pytest tests/test_variables_command.py -q
```

**Done:** All tests in `test_variables_command.py` pass. `_run_variables_batch` uses `get_model`. Reject order is video → faceswap → credentials → empty lists → `_start_job`. Allowlist comes from `model.get("provider")`. Reply Kie-ref is gated.

### Task 3: Update README `/variables` and run regressions

**type:** auto
**Objective:** User-facing docs match behavior; neighboring suites still pass.
**Files:** `README.md` L30–37
**Action:**

Rewrite the `/variables` intro so it no longer says “through the **Kie.ai** provider”. State configured image model/provider, list allowed backends, and document video + Face Swap reject. Keep N=1–10, original-image reuse, sequential/cancel/stop-on-error, and `/listas` bullets. Do not edit the Kie privacy paragraph (L28) or env table.

Then run regression + full suite.

**Verification:**

```bash
./venv/bin/python -m pytest tests/test_variables_command.py tests/test_variables_flow.py tests/test_variables_store.py tests/test_album_batch.py tests/test_cancel_job.py tests/test_kie_provider.py -q
./venv/bin/python -m pytest tests/ -q
```

Do **not** run `tests/test_live_smoke.py` against real APIs (`LIVE_SMOKE=1`).

**Done:** README `/variables` is provider-global. Regression + full `tests/` green. No-touch files unchanged.

## Instrucciones para gsd-executor

- **TDD order is mandatory.** Task 1 (failing tests) → Task 2 (impl) → Task 3 (docs + regressions). Do not implement first and “add tests after”.
- Test runner: `./venv/bin/python -m pytest`. `pytest.ini`: `asyncio_mode = auto`, `testpaths = tests`.
- **Work-unit commits** (`~/.claude/skills/work-unit-commits/SKILL.md`):
  - One conventional commit after Task 2+3 are green, including tests + README with the behavior they verify. Message: `feat(variables): run batch on configured image provider`.
  - Do not commit tests-only then impl-only.
  - Never add `Co-Authored-By` / AI attribution.
- **Patterns to copy:** `_process_single_photo_edit` L1549–1628; `handle_reply_edit` L2220–2225. Copy mechanically; do not redesign `generate_image`.
- **Anti-patterns:**
  - Forcing Kie via `_grok_model_for_config(..., "kie", ...)`.
  - Hardcoded `download_allowlist=_download_allowlist_for_provider("kie")`.
  - Preferring `_resolve_reply_kie_ref` when provider is not kie.
  - New service layer / new provider registry.
  - Mocking `get_model` or allowlist helpers.
  - Setting provider only via `sessions.set_grok_imagine_config` after hydration.
  - Touching `variables_flow.py` / `variables_store.py` / `sessions.py` / `config_flow.py` / `src/*`.
- **Logging / errors:** keep existing `print("[generate] ...")` in `generate_image` (no-touch). Batch already stops on first provider error and reuses the status message. User-facing rejects stay Spanish, same tone as existing copy.
- **Mock policy:** only Telegram I/O and provider I/O (`generate_image` / result senders). Real `get_model` + real allowlist + real regen-context builder.
- **Framework:** aiogram 3. This repo has no channel-admin / gamification / narrative systems from telegram-bot-hardener.
- **SSRF:** never pass result URLs without `_download_allowlist_for_provider(model.get("provider"))`. xAI hosts are `*.x.ai` / `*.xai.com`; Kie has its own list; Replicate allowlist is `None`.
- **Skills:** `telegram-bot-hardener`, `test-quality-flow`, `work-unit-commits`. Tests assert behavior (provider used, reject, photo downloaded), not private helper names beyond the optional `_variables_model_or_reject`.
- If you make important discoveries, save them to engram via `mem_save` with `project: 'grok'` and `topic_key: 'architecture/variables-provider'`.

## Test commands

Primary:

```bash
./venv/bin/python -m pytest tests/test_variables_command.py -q
```

Focused new/changed:

```bash
./venv/bin/python -m pytest tests/test_variables_command.py -q -k "batch or cmd_variables_reply or kie_ref"
```

Regression:

```bash
./venv/bin/python -m pytest tests/test_variables_command.py tests/test_variables_flow.py tests/test_variables_store.py tests/test_album_batch.py tests/test_cancel_job.py tests/test_kie_provider.py -q
```

Full:

```bash
./venv/bin/python -m pytest tests/ -q
```

## Risks + Mitigation

| Risk (from impact) | Mitigation | Where |
|--------------------|------------|-------|
| `grok_video` silently becomes a broken image call | Reject `key=="grok_video"` and `_comfyui_is_video` **before** credentials and `_start_job` | helper + `test_batch_rejects_grok_video` |
| `faceswap` routed through `generate_image` | Reject `key=="faceswap"` | helper + `test_batch_rejects_faceswap` |
| Reply Kie ref on non-Kie provider → txt2img + no `source_file_id` | Copy `handle_reply_edit` guard; batch also drops `kie_source_ref` when provider ≠ kie | `cmd_variables_reply` + `test_cmd_variables_reply_ignores_kie_ref_when_provider_not_kie` |
| Allowlist hardcoded `"kie"` fails xAI downloads | `_download_allowlist_for_provider(model.get("provider"))` | batch + xAI/Replicate tests |
| Kie-only credential preflight blocks xAI users | Preflight by selected provider; xAI test runs with `KIE_API_KEY=""` | helper + `test_batch_uses_xai_when_configured` |
| BytesIO cursor consumed across Replicate iterations | `image_data.seek(0)` each loop | batch + reuse test |
| Test sets disk config only | `_set_user_image_config` mutates hydrated `user_state` | Task 1 helper |
| `_run_variables_batch` already >50 lines | Tiny helper only; no service extract | A11 |

## Success Criteria

- [ ] `/variables` uses `get_model(uid)` — never `_grok_model_for_config(..., "kie", ...)`.
- [ ] Default grok+kie still works (backward compatible).
- [ ] xAI / Replicate Grok / Seedream / ComfyUI image batches run on the configured backend.
- [ ] `grok_video` and ComfyUI video rejected (message contains “video”; no `generate_image`).
- [ ] Faceswap rejected (no `generate_image`).
- [ ] Reply + Kie ref + non-kie provider downloads Telegram photo and sets `source_file_id`.
- [ ] Regen context provider matches configured provider; non-kie has `source_file_id` and no `kie_source_ref`.
- [ ] `download_allowlist=_download_allowlist_for_provider(model.get("provider"))`.
- [ ] `image_data.seek(0)` each iteration; original image reused; sequential; `delete_status=False`; cancel + stop-on-error unchanged.
- [ ] Reject order after `get_model`: video → faceswap → missing credentials → empty lists → `_start_job`.
- [ ] `./venv/bin/python -m pytest tests/test_variables_command.py -q` green.
- [ ] Regression + full `tests/` green.
- [ ] README `/variables` no longer claims Kie-only.
- [ ] No-touch list untouched: `variables_flow.py`, `variables_store.py`, `sessions.py`, `config_flow.py`, `src/*`, `generate_image` internals, video stack.
