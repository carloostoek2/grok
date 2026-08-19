---
phase: quick
plan: comfyui-refine-confirm-item3
type: auto
item: Bot — cadenas con pausa por ítem (wirear choke en call sites restantes + gap de álbum)
source: pool comfyui-refine-confirm
mode: standard
impact_ref: .grok/agent-memory/impact-analyzer/comfyui-refine-confirm-item3.md
test_command: cd /home/ubuntu/repos/grok && ./venv/bin/python -m pytest tests/test_comfyui_refine.py -q
---

## Objective

El choke de confirmación de refino ComfyUI (maquinaria del item 2, cerrada) está wireado en
UN solo call site (`_process_single_photo_edit` L1739-1757). Este ítem wirea el choke en los
4 call sites restantes de `_send_comfyui_output` y arregla el gap del álbum:

1. **L1375 regen** (`handle_regenerate_image`) — pasar `meta=kie_meta, cancel_event=cancel_event`.
2. **L1580 text-gen** (`_do_generate_text`) — pasar `meta=kie_meta, cancel_event=None`.
3. **L2191 variables** (`_run_variables_batch`) — pasar `meta=meta, cancel_event=cancel_event`.
   El await de la decisión pausa el ítem `i`; el loop continúa al `i+1` (re-check
   `_job_cancelled` en la siguiente iteración L2111/L2149). La cadena NO se rompe: combo reuse,
   `completed += 1`, resumen final intactos.
4. **L2451 reply edit** (`handle_reply_edit`) — pasar `meta=kie_meta, cancel_event=None`.
5. **Gap álbum** (`_process_album_edit_from_file_ids`, L1795-1917) — hoy usa
   `process_image_result` (L1877), que rompe para comfyui (paths locales). Rutear comfyui por
   `_send_comfyui_output` con `meta=kie_meta, cancel_event=cancel_event, delete_status=False`
   (status "Editando i/N" reusado). N fotos × (base + decisión + refino). `completed` y los
   re-checks `_job_cancelled` (L1835/L1851/L1865) intactos.

`reply edit` y `text-gen` NO inician job nuevo → `cancel_event=None` → pending entry con
`job_id=None`; el TTL (300s) la limpia; sin botón cancel en status. Decisión locked — sin scope
expansion.

Este ítem NO toca la maquinaria del item 2 (choke, TTL, B1/B2, teclados, registry) — solo
pasa `meta`/`cancel_event` desde los call sites y añade la rama comfyui al álbum.

## Scope

- **In:**
  - `bot.py`: 4 call sites (L1375/L1580/L2191/L2451) + rama comfyui en `_process_album_edit_from_file_ids`.
  - `tests/test_comfyui_refine.py` (append): casos de cadena (regen/text-gen/reply/variables/
    album ruteo + chain end-to-end).
- **Out / Non-goals:**
  - No re-abrir la maquinaria del item 2 (choke, TTL, `_send_comfyui_confirm_refine`, B1/B2,
    teclados, registry `_pending_refine`, `_finish_job`).
  - No iniciar jobs nuevos en reply edit/text-gen (cancel_event=None, locked).
  - No tocar config/sessions/variables_flow/variables_store/gen_comfy.py ni `comfyui-vast-setup`.
  - El álbum multi-ángulo de salida (5 imgs) ya lo maneja el choke (teclado en mensaje de texto
    separado, A7 del item 2).
  - No cambiar `delete_status` de regen/text-gen/reply (default True) ni de variables/álbum
    (False, ya explícito).
- **Constraints:**
  - `_send_comfyui_output` ya acepta `meta`/`cancel_event` (firma del item 2, L3580-3591) — NO
    cambiar su firma ni el choke.
  - El patrón de la rama comfyui es `_process_single_photo_edit` L1739-1757 (copiar mecánico).
  - `aioresponses` NO aplica a comfyui (subprocess SSH). Concurrencia ya resuelta en el item 2.
  - Gotcha del repo: editar sin `reply_markup` ELIMINA el teclado — el choke ya re-aplica teclados
    (item 2); no reabrir.

## Assumptions

Cerradas por impact-analyzer (inline) — no reabrir:

- **A1:** reply edit y text-gen no tienen `_start_job` (verificado: L2436/L1566 crean `status_msg`
  sin job). `cancel_event=None` → el choke registra pending con `job_id=None`; `_finish_job` de
  OTRO job del mismo user filtra por `entry["job_id"] == job_id` → no lo toca; el TTL lo limpia.
  Edge aceptado (impact).
- **A2:** En variables, la variable del loop es `meta` (L2140), NO `kie_meta`. Pasar `meta=meta`.
- **A3:** El álbum multi-ángulo de salida (comfyui devuelve lista) ya lo maneja `_send_comfyui_output`
  (branch álbum del choke, A7/A8 item 2). La rama del álbum solo añade el ruteo por provider.
- **A4:** `delete_status=False` en variables (L2199) y álbum (L1883) es EXPLÍCITO hoy y se conserva.
  En regen/text-gen/reply edit el default True se conserva (no pasar `delete_status`).
- **A5:** Ningún test existente de álbum/cancel/round5/cmd usa comfyui (verificado por rg) → la rama
  comfyui del álbum no los rompe.
- **A6:** El test `test_variables_command.py::test_batch_comfyui_uses_selected_model_and_sends_via_comfyui`
  (L927) usa asserts bare (`await_count`, `kwargs.get("delete_status")`) robustos a kwargs nuevos →
  sigue GREEN tras wiring; NO se modifica (el assert de `meta`/`cancel_event` vive en los tests
  nuevos de `test_comfyui_refine.py`).

## Architecture Approach

### QUÉ (behavior / contracts)

**Outcome:** con comfyui activo y `comfyui_refine="1"`, los 5 flujos (regen, text-gen, reply edit,
variables batch, álbum batch) envían cada resultado generado por el choke de confirmación de refino
(2 etapas) con su `meta` de remotes y su `cancel_event` correctos; el álbum rutea comfyui por
`_send_comfyui_output` en vez de `process_image_result`; y en los loops de batch (variables/álbum)
la pausa por ítem no rompe la cadena (sigue al siguiente ítem, `completed` y resumen intactos).

**Truths (must be true at the end)**

1. Los 4 call sites pasan `meta` + `cancel_event`: regen y variables pasan el `cancel_event` real
   (de `_start_job`); text-gen y reply edit pasan `cancel_event=None`.
2. Variables pasa `meta=meta` (variable del loop) y álbum pasa `meta=kie_meta`.
3. `delete_status` queda como está: True (default) en regen/text-gen/reply; False en variables/álbum.
4. El álbum comfyui llama a `_send_comfyui_output` POR foto (no `process_image_result`), con
   `delete_status=False`, y la cadena termina con "Completadas N/N" y `completed == N`.
5. En variables/álbum, tras un refino (yes) o base final (no), el loop continúa al siguiente ítem;
   un cancel durante el await se detecta en el siguiente re-check `_job_cancelled`.
6. Sin `meta` (flujos no-wired no existen ya; pero cualquier path con meta vacío) el choke NO se
   activa → comportamiento actual (base final directa).

### CÓMO (structure / patterns)

- **Layer:** todo en `bot.py` (monolito aiogram). Sin módulos nuevos.
- **Pattern to copy:** `_process_single_photo_edit` rama comfyui **L1739-1757** — el wire exacto
  `meta=kie_meta, cancel_event=cancel_event` para la rama del álbum y la firma de `_send_comfyui_output`
  (L3580-3591).
- **Interfaces / types:** `meta` shape libre (`dict | None`), `cancel_event` es `asyncio.Event | None`.
  No cambia nada.

### Exact implementation

#### 1. Call site regen — `handle_regenerate_image` (bot.py L1375-1383)

Añadir `meta=kie_meta, cancel_event=cancel_event` antes del cierre de la llamada:

```python
        if model.get("provider") == "comfyui":
            await _send_comfyui_output(
                model,
                output,
                prompt,
                status_msg,
                callback.message,
                "Edit" if mode == "edit" else "Prompt",
                regen,
                meta=kie_meta,
                cancel_event=cancel_event,
            )
            return
```

#### 2. Call site text-gen — `_do_generate_text` (bot.py L1580-1593)

Añadir `meta=kie_meta, cancel_event=None` tras el `regen_context`:

```python
        if model.get("provider") == "comfyui":
            await _send_comfyui_output(
                model,
                output,
                prompt,
                status_msg,
                message,
                "Prompt",
                _build_image_regen_context(
                    model=model,
                    user_id=uid,
                    prompt=prompt,
                    mode="text",
                ),
                meta=kie_meta,
                cancel_event=None,
            )
            return
```

#### 3. Call site variables — `_run_variables_batch` (bot.py L2191-2200)

Añadir `meta=meta, cancel_event=cancel_event` (la variable del loop es `meta`):

```python
                if use_comfyui:
                    await _send_comfyui_output(
                        model,
                        output,
                        prompt,
                        status_msg,
                        message,
                        f"Variables {i}/{count}",
                        regen_context,
                        delete_status=False,
                        meta=meta,
                        cancel_event=cancel_event,
                    )
```

#### 4. Call site reply edit — `handle_reply_edit` (bot.py L2451-2465)

Añadir `meta=kie_meta, cancel_event=None` tras el `regen_context`:

```python
        if model.get("provider") == "comfyui":
            src = message.reply_to_message.photo[-1].file_id if message.reply_to_message.photo else None
            await _send_comfyui_output(
                model,
                output,
                prompt,
                status_msg,
                message,
                "Edit",
                _build_image_regen_context(
                    model=model,
                    user_id=message.from_user.id,
                    prompt=prompt,
                    mode="edit",
                    source_file_id=src,
                ),
                meta=kie_meta,
                cancel_event=None,
            )
            return
```

#### 5. Gap álbum — `_process_album_edit_from_file_ids` (bot.py L1877-1895)

Envolver el `process_image_result` actual en un `else:` y añadir la rama comfyui (copiar el
patrón L1739-1757; `delete_status=False` igual que el `process_image_result` de álbum; usar
`anchor_message` como message):

```python
            if model.get("provider") == "comfyui":
                await _send_comfyui_output(
                    model,
                    output,
                    prompt,
                    status_msg,
                    anchor_message,
                    "Edit",
                    _build_image_regen_context(
                        model=model,
                        user_id=uid,
                        prompt=prompt,
                        mode="edit",
                        source_file_id=file_id,
                        integrate_mode=integrate_mode,
                    ),
                    delete_status=False,
                    meta=kie_meta,
                    cancel_event=cancel_event,
                )
            else:
                await process_image_result(
                    output,
                    prompt,
                    status_msg,
                    anchor_message,
                    "Edit",
                    delete_status=False,
                    download_allowlist=_download_allowlist_for_provider(model.get("provider")),
                    kie_meta=kie_meta,
                    regen_context=_build_image_regen_context(
                        model=model,
                        user_id=uid,
                        prompt=prompt,
                        mode="edit",
                        source_file_id=file_id,
                        integrate_mode=integrate_mode,
                    ),
                    model=model,
                )
```

El `completed += 1` (L1896) y el `else:` del for (resumen "Completadas N/N", L1897-1901) quedan
intactos. Los re-checks `_job_cancelled` (L1835/L1851/L1865) intactos.

## Context

- `@bot.py:1375-1383` `handle_regenerate_image` — call site 1, wire `meta=kie_meta, cancel_event=cancel_event`
- `@bot.py:1580-1593` `_do_generate_text` — call site 2, wire `meta=kie_meta, cancel_event=None`
- `@bot.py:1739-1757` `_process_single_photo_edit` — PATTERN a copiar (rama comfyui)
- `@bot.py:1795-1917` `_process_album_edit_from_file_ids` — gap álbum; `process_image_result` L1877-1895
- `@bot.py:2191-2200` `_run_variables_batch` — call site 3, wire `meta=meta, cancel_event=cancel_event`
- `@bot.py:2451-2465` `handle_reply_edit` — call site 4, wire `meta=kie_meta, cancel_event=None`
- `@bot.py:3580-3591` `_send_comfyui_output` — firma ya con `meta`/`cancel_event` (no tocar)
- `@bot.py:3685` `_send_comfyui_confirm_refine` — choke (item 2, no tocar)
- `@bot.py:691` `_refine_confirm_keyboard` / `@bot.py:713` `_register_pending_refine` — item 2, no tocar
- `@bot.py:874-919` `get_model` — devuelve `comfyui_refine` desde `sessions.get_comfyui_config` (refine default "1")
- `@tests/conftest.py:45-56` `reset_runtime_state` — ya limpia `_pending_refine` (no tocar)
- `@tests/test_comfyui_refine.py` (888 líneas) — append de casos de cadena; helpers `_status_message`,
  `_comfyui_model`, `_regen_ctx`, `_message`, `_refine_callback`
- `@tests/test_variables_command.py:927-959` `test_batch_comfyui_uses_selected_model_and_sends_via_comfyui` — revisar (GREEN sin cambios, A6)
- `@tests/test_kie_provider.py:940` `test_do_generate_text_...`, `@tests/test_kie_provider.py:1430/1462`
  `test_handle_regenerate_image_*`, `@tests/test_kie_provider.py:1494` `test_handle_reply_edit_...` —
  patrones de setup de handlers (generation_refs_file, user_state, mocks) a copiar en los tests nuevos

## Tasks

### Task 1: Tests ROJOS de cadena en `tests/test_comfyui_refine.py`

**type:** auto
**Objective:** Codificar los contratos del ítem 3 (meta/cancel_event en los 4 call sites + ruteo
comfyui del álbum + cadena que continúa tras la pausa). Todos fallan sobre el código actual.
`bot.py` NO se edita en esta task.
**Files:** `/home/ubuntu/repos/grok/tests/test_comfyui_refine.py` (append), revisar (sin editar salvo
si el runner lo exige) `tests/test_variables_command.py`
**Action:**

STRICT TDD. Capturar baseline de la suite ANTES de escribir nada:

```bash
cd /home/ubuntu/repos/grok && ./venv/bin/python -m pytest tests -q 2>&1 | tail -3
```

Baseline actual verificado: `428 passed, 2 skipped`. Luego APPENDEAR al final de
`tests/test_comfyui_refine.py` los 6 tests siguientes (helpers ya existentes en el archivo:
`_status_message`, `_comfyui_model`, `_regen_ctx`, `_message`, `_refine_callback`; usar
`bot._pending_refine.clear()` donde se toque el registry; fixtures `sessions_file`,
`generation_refs_file`, `variables_file` de conftest; `bot.user_state[uid] = {"model": "comfyui"}`
+ `sessions.set_comfyui_config(uid, model="krea2")` para que `get_model(uid)` devuelva comfyui con
`comfyui_refine="1"` — default de `sessions`):

| Test | Setup (patrón copiado) | Assert | RED por |
|------|------------------------|--------|---------|
| `test_regen_comfyui_passes_meta_and_cancel_event(generation_refs_file)` | copiar `test_handle_regenerate_image_text_mode` (test_kie_provider L1430): uid, comfyui config, `regen = _build_image_regen_context(model=bot.get_model(uid), user_id=uid, prompt="blue moon", mode="text")`, `sessions.save_generation_ref(chat, msg_id, provider="comfyui", prompt=..., regen=regen)`, photo_msg (`photo`, `chat.id`, `message_id`, `answer` AsyncMock), callback. Mock `generate_image` → `(["/tmp/c.png"], None, {"comfyui_remotes": ["/workspace/ComfyUI/output/base_a.png"]})`; mock `_send_comfyui_output` AsyncMock. `await bot.handle_regenerate_image(callback)` | `mock_send.await_args.kwargs["meta"] == {"comfyui_remotes": ["/workspace/ComfyUI/output/base_a.png"]}`; `mock_send.await_args.kwargs["cancel_event"] is not None` | call site no pasa `meta` → kwargs.get("meta") es None |
| `test_text_gen_comfyui_passes_meta_cancel_none()` | copiar `test_do_generate_text_passes_kie_image_download_allowlist` (test_kie_provider L940): msg (`from_user.id`, `answer` AsyncMock), model `_comfyui_model(key="comfyui")`. Mock `generate_image` → `(["/tmp/c.png"], None, {"comfyui_remotes": ["/workspace/ComfyUI/output/base_a.png"]})`; mock `_send_comfyui_output`. `await bot._do_generate_text(msg, model, "cat")` | `mock_send.await_args.kwargs["meta"] == {"comfyui_remotes": [...]}`; `mock_send.await_args.kwargs["cancel_event"] is None` | call site no pasa `meta` |
| `test_reply_edit_comfyui_passes_meta_cancel_none(sessions_file, generation_refs_file)` | copiar `test_handle_reply_edit_uses_kie_task_id_for_video` (test_kie_provider L1494): uid, comfyui config, `bot.user_state[uid] = {"model": "comfyui"}`, reply_msg (`photo=[MagicMock(file_id="fid")]`, chat.id, message_id), message (`text="cambia el fondo"`, reply_to_message=reply_msg, answer AsyncMock). Mock `_download_telegram_photo` AsyncMock; mock `generate_image` → `(["/tmp/c.png"], None, {"comfyui_remotes": [...]})`; mock `_send_comfyui_output`. `await bot.handle_reply_edit(message)` | `mock_send.await_args.kwargs["meta"] == {"comfyui_remotes": [...]}`; `mock_send.await_args.kwargs["cancel_event"] is None` | call site no pasa `meta` |
| `test_variables_batch_comfyui_passes_meta_and_cancel_event(sessions_file, variables_file, monkeypatch)` | copiar `test_batch_comfyui_uses_selected_model_and_sends_via_comfyui` (test_variables_command L927): `monkeypatch.setattr(bot, "COMFYUI_HOST", "1.2.3.4")`, `bot.user_state[1001] = {"model": "comfyui"}`, `msg = _make_photo_message(caption="/variables 2")` con `answer.return_value = _make_status()`. `_fake_gen` → `(["/tmp/v.png"], None, {"comfyui_remotes": ["/workspace/ComfyUI/output/base_a.png"]})`; mock `_send_comfyui_output` AsyncMock `side_effect=[True, True]`; mock `process_image_result`. Patch `variables_store.random_combination`. `await bot._run_variables_batch(msg, 2, BytesIO(b"img"), None, source_file_id="p1")` | `mock_send.await_count == 2`; por call: `call.kwargs["meta"] == {"comfyui_remotes": [...]}` y `call.kwargs["cancel_event"] is not None` y `call.kwargs["delete_status"] is False`; resumen final contiene "Listo: 2/2" | call site no pasa `meta`/`cancel_event` |
| `test_album_batch_comfyui_routes_to_send_comfyui_output(sessions_file, generation_refs_file, monkeypatch)` | uid, comfyui config (`sessions.set_comfyui_config(uid, model="krea2")`), `bot.user_state[uid] = {"model": "comfyui"}`, anchor_msg estilo `_make_album_message` (test_album_batch L50: `from_user.id`, `chat.id`, `message_id`, `photo`, `reply = AsyncMock(return_value=status)`, `answer = AsyncMock()`). Mock `_download_telegram_file_id` AsyncMock; `_fake_gen` → `(["/tmp/a.png"], None, {"comfyui_remotes": ["/workspace/ComfyUI/output/base_a.png"]})`; mock `_send_comfyui_output` AsyncMock `side_effect=[True, True]`; mock `process_image_result` AsyncMock. `await bot._process_album_edit_from_file_ids(anchor_msg, "cambia el fondo", ["p1", "p2"])` | `mock_send.await_count == 2`; por call: `call.kwargs["meta"]` con remotes y `call.kwargs["cancel_event"] is not None` y `call.kwargs["delete_status"] is False`; `mock_proc.assert_not_awaited()`; status final editado a "Completadas 2/2" | álbum NO rutea comfyui → llama `process_image_result` (mock_proc.await_count > 0 → assert_not_awaited falla) |
| `test_variables_batch_comfyui_chain_continues_after_decision(sessions_file, variables_file, monkeypatch)` | CHAIN end-to-end con el choke REAL (patrón `test_send_comfyui_output_confirm_yes_single`, test_comfyui_refine L222): setup igual al anterior pero SIN mockear `_send_comfyui_output`. Patch `_send_comfyui_image` AsyncMock `side_effect=[base_msg, refined_msg]` (MagicMocks con `delete`/`edit_reply_markup` AsyncMock); patch `_generate_comfyui_refine` AsyncMock → `(["/tmp/refined.png"], None)`; patch `_send_comfyui_album` AsyncMock. `_fake_gen` → `(["/tmp/v.png"], None, {"comfyui_remotes": ["/workspace/ComfyUI/output/base_a.png"]})`. Lanzar `task = asyncio.create_task(bot._run_variables_batch(msg, 2, BytesIO(b"img"), None, source_file_id="p1"))`. **Ítem 1:** loop `for _ in range(200): if bot._pending_refine: break; await asyncio.sleep(0)`; `token1 = next(iter(bot._pending_refine))`; `await bot.handle_refine_decision(_refine_callback(uid, f"refine:{token1}:yes"))`. **Ítem 2:** volver a pollear `_pending_refine` hasta un token distinto; `await bot.handle_refine_decision(_refine_callback(uid, f"refine:{token2}:no"))`. `await asyncio.wait_for(task, timeout=10)` | `_generate_comfyui_refine` awaited UNA vez (solo ítem 1); `_send_comfyui_image` await_count >= 3 (base+refinado ítem 1, base ítem 2); resumen final "Listo: 2/2" (msg.answer.return_value.edit_text); `completed == 2` implícito en el resumen | call site no pasa `meta` → el choke NO se activa → `_pending_refine` nunca se llena → timeout/pendiente = RED |

Correr y confirmar que TODOS fallan (RED) sobre el código actual:

```bash
cd /home/ubuntu/repos/grok && ./venv/bin/python -m pytest tests/test_comfyui_refine.py -q 2>&1 | tail -8
```

Revisar (sin editar salvo fallo real) `test_variables_command.py::test_batch_comfyui_uses_selected_model_and_sends_via_comfyui`
(L927): tras el wiring debe seguir GREEN (asserts bare, A6).

**Verification:**

```bash
cd /home/ubuntu/repos/grok && ./venv/bin/python -m pytest tests/test_comfyui_refine.py -q
```

**Done:** Los 6 tests nuevos existen y fallan/erran sobre el código actual (RED por la columna
"RED por"). `bot.py` NO fue editado. Baseline de la suite capturado (428 passed, 2 skipped).

### Task 2: Implementar los 5 cambios en `bot.py`

**type:** auto
**Objective:** `bot.py` cumple los contratos del harness; suite del harness verde.
**Files:** `/home/ubuntu/repos/grok/bot.py` (5 ediciones, ver "Architecture Approach § Exact implementation")
**Action:**

Aplicar EXACTAMENTE los 5 bloques de "Exact implementation", en orden: regen (1), text-gen (2),
variables (3), reply edit (4), álbum (5). Copiar el patrón del bloque 1 para la rama del álbum (5).

NO:
- Tocar la maquinaria del item 2 (`_send_comfyui_output` L3580, `_send_comfyui_confirm_refine` L3685,
  teclados, registry `_pending_refine`, `_finish_job`, `handle_refine_decision`).
- Cambiar `delete_status` de ningún call site.
- Tocar config/sessions/variables_flow/variables_store/gen_comfy.py.
- Añadir dependencias (solo stdlib + aiogram ya importados).

Después, re-correr el harness hasta verde:

```bash
cd /home/ubuntu/repos/grok && ./venv/bin/python -m pytest tests/test_comfyui_refine.py -q
```

Syntax check:

```bash
cd /home/ubuntu/repos/grok && ./venv/bin/python -c "import ast; ast.parse(open('bot.py').read()); print('ok')"
```

**Verification:**

```bash
cd /home/ubuntu/repos/grok && ./venv/bin/python -m pytest tests/test_comfyui_refine.py -q
cd /home/ubuntu/repos/grok && ./venv/bin/python -m pytest tests/test_variables_command.py::test_batch_comfyui_uses_selected_model_and_sends_via_comfyui tests/test_album_batch.py -q
cd /home/ubuntu/repos/grok && ./venv/bin/python -c "import ast; ast.parse(open('bot.py').read()); print('ok')"
```

**Done:** Los 6 tests nuevos pasan (los 12+ del item 2 siguen verdes). `test_batch_comfyui_...` y
`test_album_batch.py` verdes. Sintaxis OK. No-touch intacto (verificar `git diff --stat`).

### Task 3: Regresión + commit work-unit

**type:** auto
**Objective:** La suite completa no introduce regresiones vs baseline de Task 1; commit único.
**Files:** `/home/ubuntu/repos/grok/bot.py`, `/home/ubuntu/repos/grok/tests/test_comfyui_refine.py`
**Action:**

Correr la suite completa y comparar con el baseline (428 passed, 2 skipped; no deben aparecer
fallos NUEVOS):

```bash
cd /home/ubuntu/repos/grok && ./venv/bin/python -m pytest tests -q 2>&1 | tail -3
```

Si aparece un fallo nuevo en un archivo que este ítem NO tocó (variables/album/kie/cancel),
parar e investigar antes de commitear — no commitear con regresión nueva.

Commit único (work-unit) con los 2 archivos:

```bash
cd /home/ubuntu/repos/grok
git add bot.py tests/test_comfyui_refine.py
git commit -m "feat(comfyui): wire refine confirmation into batch chains"
```

Sin `Co-Authored-By`. Verificar con `git status --short` y `git log --oneline -1`.

**Verification:**

```bash
cd /home/ubuntu/repos/grok && ./venv/bin/python -m pytest tests/test_comfyui_refine.py -q
cd /home/ubuntu/repos/grok && git status --short
cd /home/ubuntu/repos/grok && git log --oneline -1
```

**Done:** Harness verde. Suite completa sin fallos nuevos vs baseline. Commit único convencional,
sin `Co-Authored-By`; `git status` limpio (solo el commit nuevo).

## Instrucciones para gsd-executor

- **TDD order es obligatorio:** Task 1 (tests RED) → Task 2 (impl) → Task 3 (regresión + commit).
  No implementar primero.
- **Repo y runner:** cambio en `/home/ubuntu/repos/grok` (monolito aiogram). Runner:
  `cd /home/ubuntu/repos/grok && ./venv/bin/python -m pytest` (pytest 8.4.2, `asyncio_mode = auto`;
  NO hace falta `@pytest.mark.asyncio`). Baseline de la suite: 428 passed, 2 skipped (capturar al
  inicio de Task 1).
- **Skills a cargar ANTES de trabajar** (leer el SKILL.md completo de cada una):
  - `telegram-bot-hardener` — handlers aiogram + registry runtime + teclados; respetar reglas de
    handlers acotados y tests (pytest + pytest-asyncio/auto).
  - `work-unit-commits` — commit único work-unit, conventional, sin `Co-Authored-By`.
  - `test-quality-flow` — los tests deben validar comportamiento (los 6 de Task 1), no documentar
    código roto.
- **Patterns to copy (mecánico, no rediseñar):** `_process_single_photo_edit` rama comfyui L1739-1757
  (wire del call site); `_send_comfyui_output` L3580 (firma, no tocar); tests de handlers en
  `test_kie_provider.py` L940/1430/1462/1494 (setup); patrón de poll `_pending_refine` en
  `test_comfyui_refine.py` L222 (chain test).
- **Gotchas (no pisar):**
  - En variables la variable del loop es `meta` (L2140), NO `kie_meta` — pasar `meta=meta`.
  - En álbum el message del choke es `anchor_message` (no `message`) y `delete_status=False`.
  - El choke espera `meta["comfyui_remotes"]` NO vacío para activarse; sin remotes → base directa.
  - `reply edit`/`text-gen`: `cancel_event=None` (locked) — NO inventar `_start_job`.
  - NO mockear `os.path.exists` en el harness; usar `tmp_path` para `_comfyui_pull` real.
  - `aioresponses` NO aplica (comfyui usa subprocess SSH). No usar HTTP mocks.
  - El chain test (Task 1) usa el choke REAL: el `_pending_refine` se llena en el await — pollear
    con `await asyncio.sleep(0)` y límite de iteraciones; resolver con `handle_refine_decision`.
- **No-touch verificable con `git diff --stat`:** `_send_comfyui_output`, `_send_comfyui_confirm_refine`,
  `_generate_comfyui`, `_generate_comfyui_refine`, teclados/registry del item 2, `get_model`,
  `process_image_result`, `_process_single_photo_edit`, variables_flow/variables_store/sessions/config,
  gen_comfy.py/comfyui-vast-setup.
- **Commit:** mensaje `feat(comfyui): wire refine confirmation into batch chains`, SIN
  `Co-Authored-By`. Un solo commit con `bot.py` + `tests/test_comfyui_refine.py`.
- Si descubres gotchas no obvios, guárdalos en engram vía `mem_save` con `project: 'grok'` y
  `topic_key: 'architecture/comfyui-refine-confirm'`.

## Test commands

Harness (primario):

```bash
cd /home/ubuntu/repos/grok && ./venv/bin/python -m pytest tests/test_comfyui_refine.py -q
```

Variables + álbum (regresión acotada):

```bash
cd /home/ubuntu/repos/grok && ./venv/bin/python -m pytest tests/test_variables_command.py tests/test_album_batch.py -q
```

Regresión completa:

```bash
cd /home/ubuntu/repos/grok && ./venv/bin/python -m pytest tests -q
```

Syntax check:

```bash
cd /home/ubuntu/repos/grok && ./venv/bin/python -c "import ast; ast.parse(open('bot.py').read()); print('ok')"
```

## Risks + Mitigation

| Risk (from impact) | Mitigation | Where |
|--------------------|------------|-------|
| MEDIUM — reply edit/text-gen sin job: pending entry con `job_id=None`; un cancel de OTRO job NO la resuelve (B1 filtra por job_id); TTL 300s la limpia | `cancel_event=None` + TTL existente (item 2); edge aceptado por impact; no hay botón cancel en status | A1; wiring L1580/L2451 |
| MEDIUM — El await de decisión pausa el ítem del batch; si el usuario cancela durante el await, la cadena debe continuar (no colgar) | El choke re-chequea `_job_cancelled` tras el await (B2 item 2) y los loops re-chequean en la siguiente iteración (variables L2111/L2149; álbum L1835/L1851/L1865) | A2/A5; `test_variables_batch_comfyui_chain_continues_after_decision` |
| MEDIUM — Álbum comfyui ruteado mal (olvidar `delete_status=False` borra el status "Editando i/N" y rompe la cadena) | `delete_status=False` explícito en la rama comfyui del álbum (igual que `process_image_result` L1883) | bloque 5; `test_album_batch_comfyui_routes_to_send_comfyui_output` |
| MEDIUM — En variables, pasar `kie_meta` en vez de `meta` (variable del loop) → `meta` vacío → choke NO se activa | Nombre de variable verificado (L2140 `output, err, meta`) — instrucción explícita `meta=meta` | bloque 3; tests de variables |
| LOW — Regresión: `_send_comfyui_output` ya recibe `meta` desde más call sites → el choke se activa en flujos que antes mandaban base directa | Es EL objetivo del ítem; los tests de cadena lo validan; no-touch de la maquinaria (item 2) | Success criteria |
| LOW — Test chain end-to-end frágil (timing del poll de `_pending_refine`) | Patrón ya probado en item 2 (L222-280); loop `for _ in range(200): if _pending_refine: break; await sleep(0)` + `asyncio.wait_for(task, timeout=10)` | `test_variables_batch_comfyui_chain_continues_after_decision` |
| LOW — Aserts existentes de variables/álbum podrían romper por kwargs nuevos | Asserts bare (`await_count`, `kwargs.get`) robustos (verificado A6); `test_album_batch.py` no usa comfyui (A5) | Task 2 verification |

## Success Criteria

- [ ] Los 4 call sites (L1375 regen, L1580 text-gen, L2191 variables, L2451 reply) pasan
      `meta` + `cancel_event` (regen/variables: evento real; text-gen/reply: None).
- [ ] Variables pasa `meta=meta` (variable del loop); álbum rutea comfyui por `_send_comfyui_output`
      con `meta=kie_meta, cancel_event=cancel_event, delete_status=False`.
- [ ] `delete_status` intacto: True (default) en regen/text-gen/reply; False en variables/álbum.
- [ ] En los loops de batch, la pausa por ítem NO rompe la cadena: `completed`, resumen final
      ("Listo: N/N" / "Completadas N/N") y combo reuse intactos; cancel durante el await se detecta
      en el siguiente re-check `_job_cancelled`.
- [ ] Álbum comfyui: `process_image_result` NO se llama para comfyui (ruteo correcto).
- [ ] Maquinaria del item 2 y no-touch intactos (verificar `git diff --stat`).
- [ ] `tests/test_comfyui_refine.py`: 6 tests nuevos RED en Task 1, GREEN en Task 2 y 3; los tests
      del item 2 siguen verdes.
- [ ] Suite completa sin fallos NUEVOS vs baseline (428 passed, 2 skipped); commit único
      `feat(comfyui): wire refine confirmation into batch chains` sin `Co-Authored-By`;
      `git status` limpio.
