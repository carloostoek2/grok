---
phase: quick
plan: comfyui-refine-confirm-item4
type: auto
item: Tests diferidos (a)-(f) + README + deploy note (cierre del pool)
source: pool comfyui-refine-confirm
mode: standard
impact_ref: .grok/agent-memory/impact-analyzer/comfyui-refine-confirm-item4.md
test_command: cd /home/ubuntu/repos/grok && ./venv/bin/python -m pytest tests/test_comfyui_refine.py -q
---

## Objective

Cerrar el pool `comfyui-refine-confirm` cubriendo los branches que quedaron fuera del DoD de los
items 2-3 (hallazgo T-S5 del review del item 2 + residuales del item 3) y documentar el flujo en
el README del bot. **NO hay implementación nueva**: la maquinaria del refino (choke,
`_send_comfyui_confirm_refine`, TTL, B1/B2, teclados, registry `_pending_refine`) ya está en
producción (items 2-3 cerrados). Este ítem es SOLO tests + docs; `bot.py` se toca ÚNICAMENTE si un
test diferido expone un bug real (scope acotado).

Tres partes:

1. **Parte A — tests diferidos** en `tests/test_comfyui_refine.py` (append, con el choke REAL;
   mock solo bordes externos). Branches (a)-(f).
2. **Parte B — README del bot** (`/home/ubuntu/repos/grok/README.md`): flujo de confirmación de
   refino ComfyUI.
3. **Parte C — deploy note**: el flujo requiere `REFINE_ONLY` en el box (`gen_comfy.py` actualizado,
   `cp gen_comfy.py /workspace/gen_comfy.py`), referencia a `comfyui-vast-setup`. Deploy = acción
   manual del operador, NO se ejecuta.

## Scope

- **In:**
  - `tests/test_comfyui_refine.py` (append): 7 funciones de test (8 casos, (c) parametrizada).
  - `README.md` (grok): sección ComfyUI + fila `REFINE_CONFIRM_TIMEOUT` en la tabla de env +
    deploy note.
  - `bot.py`: SOLO si un test (a)-(f) expone un bug real (fix acotado al bug, sin expandir).
- **Out / Non-goals:**
  - No tocar la maquinaria del refino (item 2: choke, TTL, `_send_comfyui_confirm_refine`,
    `handle_refine_decision`, `handle_cancel_job`, teclados, registry, `_finish_job`).
  - No tocar el wiring de call sites (item 3) ni `_process_album_edit_from_file_ids` salvo bug
    expuesto.
  - No tocar `comfyui-vast-setup` (el deploy es manual, item 1 cerrado).
  - No desplegar / no ejecutar el `cp gen_comfy.py` (Parte C es documentación).
  - No re-abrir hallazgos ya cerrados del review (B1/B2/G-S1..S4/G-N1..N2 del item 2).
  - NO afirmar en el README que los álbumes comfyui multi-foto funcionan (rama defensiva/dead:
    `handle_album` no rutea comfyui; `_process_album_edit_from_file_ids` L1881-1885 documentado como
    dead branch).
- **Constraints:**
  - Los tests usan el choke REAL: NO se mockea `_send_comfyui_output` ni `_send_comfyui_confirm_refine`
    ni `handle_refine_decision` ni `handle_cancel_job`. Mock solo bordes externos:
    `_generate_comfyui_refine`, senders (`_send_comfyui_image`, `_send_comfyui_album`),
    `generate_image`, `_download_telegram_file_id`/`_download_telegram_photo`. TTL vía
    `monkeypatch.setattr(bot, "REFINE_CONFIRM_TIMEOUT", 0)`.
  - `aioresponses` NO aplica (comfyui usa subprocess SSH, no HTTP).
  - Baseline de la suite (verificado 2026-08-19): `435 passed, 2 skipped`.

## Assumptions

- **A1:** Los branches (a)-(f) están YA implementados en producción (items 2-3). Al escribir los
  tests, la mayoría pasará a la primera (GREEN como guard de regresión). Si un test FALLA → expone
  un bug real → el executor lo reporta y lo arregla DENTRO del ítem (scope acotado al branch del
  test), NO expande.
- **A2:** El branch de cancel (a) es el `decision is _REFINE_CANCELLED` de `_send_comfyui_confirm_refine`
  (bot.py L3894-3906): single → `base_msg.edit_reply_markup(reply_markup=None)` (teclado QUITADO, no
  regen kb); álbum → `confirm_msg.delete()`. `return True`. El `status_msg` NO se borra en este
  branch (el `delete_status` de L3920 queda fuera; el handler de cancel es dueño del status).
- **A3:** El branch de refine-failure (b) es el `if rerr:` de `_send_comfyui_confirm_refine`
  (bot.py L3829-3842): single → `status_msg.edit_text(rerr, reply_markup=None)` + base restaurada a
  `_image_regenerate_keyboard()` (base NO se borra); álbum → `confirm_msg.delete()` + base álbum
  conservada. `return True`.
- **A4:** El branch de álbum no/timeout (c) es el final de `_send_comfyui_confirm_refine`
  (bot.py L3908-3925): `safe_edit_text(confirm_msg, "Imagen final.")` (SIN reply_markup — la base
  álbum no tiene teclado) + `status_msg.delete()` si `delete_status` (default True). La decisión `no`
  y el TTL=0 llegan al MISMO código (decisión falsy).
- **A5:** En el álbum batch (e), cada foto genera `output = [path]` (len 1) → `_send_comfyui_confirm_refine`
  toma el path SINGLE por foto (base imagen con teclado de confirmación encima, no álbum). La cadena
  es secuencial: el await del choke de la foto `i` bloquea hasta la decisión; luego continúa a `i+1`.
  El resumen final es `status_msg.edit_text(f"Completadas {n}/{n} imágenes.")` (else de `if err:`,
  L1927-1931).
- **A6:** En cancel mid-chain de variables (f), el cancel (event set + future resuelto a
  `_REFINE_CANCELLED`) hace que `_send_comfyui_confirm_refine` devuelva True (branch A2) y el loop
  siga a la iteración siguiente, donde `_job_cancelled` (L2141) corta con
  `"⏹ Cancelado. Completadas {completed}/{count} imágenes."` (L2142-2146). Observado: `completed`
  suma 1 tras el ítem cancelado (el choke devuelve True, sin excepción) → X=1 en el test. El
  contrato esencial es: el loop PARA (item 2 nunca genera) + status "Cancelado." — NO fijar X exacto
  en el assert.
- **A7:** El test (a) dispara el cancel de forma end-to-end vía `handle_cancel_job` (el patrón
  `force-resolve` del item 2). Para eso el evento debe estar registrado en `_active_jobs` (usar
  `bot._start_job(uid, "edit")`, igual que `test_handle_cancel_job_resolves_pending_refine`).
- **A8:** En los tests de álbum (c/e), `message.answer(...)` del choke (confirm prompt) solo se
  invoca en el path álbum real (output len > 1). En el path single NO hay confirm prompt separado
  (el teclado va en la imagen). Para (e) (single por foto) `anchor_msg.answer` NO se usa como
  confirm; el status es `anchor_msg.reply.return_value`.

## Architecture Approach

### QUÉ (behavior / contracts)

**Outcome:** Los 6 branches diferidos (cancel del flujo, refine-failure single/álbum, álbum
no/timeout → "Imagen final.", choke-negative con meta=None, álbum batch con choke real por foto,
cancel mid-chain de /variables) quedan cubiertos con el choke REAL; el README documenta el flujo de
confirmación y la dependencia de deploy; un work-unit commit cierra el pool.

**Truths (must be true at the end)**

1. (a) Cancel con decisión resuelta: single → teclado del base a `None` (NO regen kb), sin refino
   (`_generate_comfyui_refine` no se await), task `True`, `_pending_refine` limpio, `status_msg`
   no borrado por el choke.
2. (b) `_generate_comfyui_refine` → `(None, err)`: single → base conservada con
   `_image_regenerate_keyboard()` y `status_msg` con el error; álbum → `confirm_msg` borrado y base
   álbum conservada. Task `True` en ambos.
3. (c) Álbmum + decisión `no` o TTL=0 → `confirm_msg` editado a "Imagen final." (sin teclado);
   `status_msg` borrado (delete_status True); base álbum conservada; sin refino.
4. (d) `_send_comfyui_output` con `meta=None` (comfyui_refine="1") → base final directa
   (`_send_comfyui_image` una vez, kb regen default), sin confirm, `_pending_refine` vacío
   (comportamiento pre-ítem, sin regresión).
5. (e) Álbum batch (`_process_album_edit_from_file_ids`, 2 fotos) con choke REAL: cadena por foto
   (base + decisión + refino), `_generate_comfyui_refine` await una vez (ítem 1 yes; ítem 2 no),
   resumen final "Completadas 2/2", sin hang, job finalizado.
6. (f) Cancel durante la cadena de /variables: el loop para limpio con
   "⏹ Cancelado. Completadas X/N" (sin "Listo:"), `generate_image` await SOLO del ítem 1, sin refino,
   sin hang, job finalizado.
7. README documenta: base → `[✨ Refinar][⏭ Continuar]` → refino de la MISMA base; Continuar =
   base final; TTL 300s (env `REFINE_CONFIRM_TIMEOUT`) → base final; cancel respeta la cadena de
   /variables; NO afirma que los álbumes comfyui multi-foto funcionan; deploy note del `REFINE_ONLY`.
8. `bot.py` sin cambios salvo bug expuesto por (a)-(f); suite completa sin fallos NUEVOS vs baseline
   (435 passed, 2 skipped); commit único convencional sin `Co-Authored-By`.

### CÓMO (structure / patterns)

- **Layer:** tests en `tests/test_comfyui_refine.py` (monolito ya existente, 1168 líneas, helpers
  listos); docs en `README.md` del bot.
- **Pattern to copy:**
  - Poll de `_pending_refine` + resolución con `handle_refine_decision`: `test_comfyui_refine.py`
    L222-280 (`test_send_comfyui_output_confirm_yes_single`).
  - Cancel end-to-end vía `handle_cancel_job`: `test_comfyui_refine.py` L866-885
    (`test_handle_cancel_job_resolves_pending_refine`).
  - Chain de variables con choke REAL: `test_comfyui_refine.py` L1116-1167
    (`test_variables_batch_comfyui_chain_continues_after_decision`).
  - Setup de álbum batch: `test_comfyui_refine.py` L1090-1113
    (`test_album_batch_comfyui_routes_to_send_comfyui_output`).
- **Interfaces / types:** sin cambio de firmas. `meta` = `dict | None` con `comfyui_remotes`;
  `cancel_event` = `asyncio.Event | None`; `REFINE_CONFIRM_TIMEOUT` = int env.

### Mapa de branches a código de producción (para asserts)

| Branch | Código | Assert clave |
|--------|--------|--------------|
| (a) cancel single | `_send_comfyui_confirm_refine` L3894-3906 | `base_msg.edit_reply_markup(reply_markup=None)`; refine NOT awaited; `_pending_refine` vacío |
| (b) refine-error single | L3829-3841 | `status_msg.edit_text(rerr)`; `base_msg.edit_reply_markup(_image_regenerate_keyboard())`; `base_msg.delete` NOT awaited |
| (b) refine-error álbum | L3829-3841 | `confirm_msg.delete` awaited; `_send_comfyui_album` count 1 (solo base); `status_msg.edit_text(rerr)` |
| (c) álbum no/timeout | L3908-3925 | `confirm_msg.edit_text("Imagen final.")`; `status_msg.delete` awaited; refine NOT awaited |
| (d) choke-negative meta=None | `_send_comfyui_output` L3636-3658 | `_send_comfyui_image` count 1; `_send_comfyui_confirm_refine` NOT awaited; `_pending_refine` vacío |
| (e) álbum chain choke real | `_process_album_edit_from_file_ids` L1886-1905 + resumen L1927-1931 | refine await 1; `_send_comfyui_image` count 3; status "Completadas 2/2" |
| (f) cancel mid-chain | `_run_variables_batch` L2140-2254 + `_start_job`/`_finish_job` | status "⏹ Cancelado." + "Completadas"; `generate_image` await 1; sin "Listo:" |

## Context

- `@bot.py:3894-3906` `_send_comfyui_confirm_refine` — branch cancel (a): single → kb a None; álbum → confirm_msg.delete
- `@bot.py:3829-3842` `_send_comfyui_confirm_refine` — branch refine-failure (b): rerr → status + base/confirm
- `@bot.py:3908-3925` `_send_comfyui_confirm_refine` — álbum no/timeout (c): `safe_edit_text(confirm_msg, "Imagen final.")` + delete_status
- `@bot.py:3636-3658` `_send_comfyui_output` — choke condition (d): `meta is None` → skip confirm
- `@bot.py:1886-1905` `_process_album_edit_from_file_ids` — rama comfyui (e): `_send_comfyui_output` por foto; resumen `else` L1927-1931
- `@bot.py:2140-2254` `_run_variables_batch` — loop de variables (f): re-checks `_job_cancelled` L2141/2179/2198; `completed += 1` L2249
- `@bot.py:1170-1187` `handle_cancel_job` — force-resolve del future a `_REFINE_CANCELLED` (a/f)
- `@bot.py:730-744` `_cancel_pending_refines_for_user` — resuelve futures por job_id
- `@bot.py:922` `safe_edit_text` — edita "Imagen final." sin reply_markup (c)
- `@bot.py:682` `_image_regenerate_keyboard` / `@bot.py:691` `_refine_confirm_keyboard` / `@bot.py:702` `_refining_keyboard`
- `@tests/test_comfyui_refine.py` (1168 líneas) — append; helpers `_status_message`, `_comfyui_model`,
  `_regen_ctx`, `_message`, `_refine_callback`, `_photo_message`, `_album_message`, `_COMFYUI_REMOTES`
- `@tests/conftest.py:45-56` `reset_runtime_state` — autouse, limpia `_pending_refine`/`_active_jobs` (no tocar)
- `@README.md` (grok, 73 líneas) — secciones: Models, Video generation, /variables, Environment variables, Deployment, Tests

## Tasks

### Task 1: Tests diferidos (a)-(f) en `tests/test_comfyui_refine.py`

**type:** auto
**Objective:** Codificar los contratos de los 6 branches diferidos con el choke REAL (mock solo
bordes externos). Los tests se escriben ANTES que el README. Como la producción ya existe, cada test
debe pasar (GREEN); si uno FALLA → expone un bug real → reportarlo y arreglarlo dentro del ítem
(scope acotado al branch del test), re-correr hasta GREEN. `bot.py` NO se edita si no hay bug.
**Files:** `/home/ubuntu/repos/grok/tests/test_comfyui_refine.py` (append)
**Action:**

STRICT TDD. Capturar baseline de la suite ANTES de escribir nada:

```bash
cd /home/ubuntu/repos/grok && ./venv/bin/python -m pytest tests -q 2>&1 | tail -3
```

Baseline actual verificado: `435 passed, 2 skipped`. Luego APPENDEAR al final de
`tests/test_comfyui_refine.py` las 7 funciones siguientes (helpers ya existentes en el archivo:
`_status_message`, `_comfyui_model`, `_regen_ctx`, `_message`, `_refine_callback`, `_photo_message`,
`_album_message`, `_COMFYUI_REMOTES`; usar `bot._pending_refine.clear()` al inicio de cada test que
toque el registry; fixtures `sessions_file`, `generation_refs_file`, `variables_file` de conftest;
`bot.user_state[uid] = {"model": "comfyui"}` + `sessions.set_comfyui_config(uid, model="krea2")` para
`get_model(uid)` → comfyui con `comfyui_refine="1"`; `monkeypatch.setattr(bot, "COMFYUI_HOST",
"1.2.3.4")` en los tests de batch):

| Test (branch) | Setup (patrón copiado) | Assert | RED si |
|---------------|------------------------|--------|--------|
| `test_send_comfyui_output_confirm_cancel_removes_keyboard` (a) | copiar L866-885 (`test_handle_cancel_job_resolves_pending_refine`) + L224-280 (poll). `bot._pending_refine.clear()`; `event = bot._start_job(uid, "edit")` (uid=6021); `status_msg = _status_message()`; `message = _message(uid, 91)`; `base_msg` MagicMock con `delete`/`edit_reply_markup` AsyncMock. Patch `_send_comfyui_image` AsyncMock `side_effect=[base_msg]`; patch `_generate_comfyui_refine` AsyncMock. Lanzar `task = asyncio.create_task(bot._send_comfyui_output(_comfyui_model(), "/tmp/base.png", "prompt", status_msg, message, "Edit", _regen_ctx(uid), meta=dict(_COMFYUI_REMOTES), cancel_event=event))`. Poll `for _ in range(200): if bot._pending_refine: break; await asyncio.sleep(0)`; `token = next(iter(bot._pending_refine))`. `cb = MagicMock(); cb.from_user.id = uid; cb.data = f"cancel_job:{event.job_id}"; cb.answer = AsyncMock(); cb.message = status_msg`. `await bot.handle_cancel_job(cb)`. `await asyncio.wait_for(task, timeout=5)` | `result is True`; `refine_mock.assert_not_awaited()`; `send_img.await_count == 1`; `base_msg.edit_reply_markup` awaited una vez con `reply_markup=None`; `base_msg.delete.assert_not_awaited()`; `status_msg.delete.assert_not_awaited()`; `not bot._pending_refine` | el branch cancel restaura regen kb en vez de None, o refina, o cuelga |
| `test_send_comfyui_output_confirm_refine_error_single_keeps_base` (b-single) | copiar L224-280 (yes single). `event = asyncio.Event(); event.job_id = "jobb1"`. `base_msg` con `delete`/`edit_reply_markup` AsyncMock. Patch `_send_comfyui_image` AsyncMock `side_effect=[base_msg]`; patch `_generate_comfyui_refine` AsyncMock `return_value=(None, "Configuración de refino inválida")`. Lanzar task con `meta=dict(_COMFYUI_REMOTES), cancel_event=event`; poll token; `await bot.handle_refine_decision(_refine_callback(uid, f"refine:{token}:yes"))`; `await asyncio.wait_for(task, timeout=5)` | `result is True`; `refine_mock.assert_awaited_once()`; `send_img.await_count == 1`; `status_msg.edit_text` último call: primer arg contiene "Configuración de refino inválida" y `reply_markup=None`; `base_msg.edit_reply_markup` awaited con `reply_markup=_image_regenerate_keyboard()`; `base_msg.delete.assert_not_awaited()` | el branch de error borra el base, o no surfacea el error, o no restaura regen kb |
| `test_send_comfyui_output_confirm_refine_error_album_keeps_base` (b-album) | copiar L377-439 (yes álbum) pero con refine que falla. `confirm_msg` MagicMock con `delete`/`edit_text` AsyncMock; `message.answer = AsyncMock(return_value=confirm_msg)`. `base_msgs = [MagicMock(), MagicMock()]` con `delete` AsyncMock. Patch `_send_comfyui_album` AsyncMock `side_effect=[base_msgs]`; patch `_generate_comfyui_refine` AsyncMock `return_value=(None, "El refino no produjo imágenes")`. `output=["/tmp/a.png", "/tmp/b.png"]`. Poll token; `await bot.handle_refine_decision(_refine_callback(uid, f"refine:{token}:yes"))`; `await asyncio.wait_for(task, timeout=5)` | `result is True`; `send_album.await_count == 1`; `confirm_msg.delete.assert_awaited_once()`; `status_msg.edit_text` último call contiene el error; para cada base_msg: `m.delete.assert_not_awaited()` | el branch de error álbum borra el base álbum, o envía un álbum refinado, o no borra el confirm |
| `test_send_comfyui_output_confirm_album_final_image[no|timeout]` (c, parametrizada) | copiar L377-439 (yes álbum) pero decisión falsy. `@pytest.mark.parametrize("trigger", ["no", "timeout"])`. `confirm_msg` con `edit_text`/`delete` AsyncMock; `message.answer = AsyncMock(return_value=confirm_msg)`; `base_msgs` con `delete` AsyncMock; patch `_send_comfyui_album` AsyncMock `side_effect=[base_msgs]`; patch `_generate_comfyui_refine` AsyncMock. Caso `no`: poll token → `handle_refine_decision(_refine_callback(uid, f"refine:{token}:no"))`. Caso `timeout`: `monkeypatch.setattr(bot, "REFINE_CONFIRM_TIMEOUT", 0)` y NO resolver (el wait_for(task, timeout=5) dispara el TTL; patrón L335-375 single-timeout). `await asyncio.wait_for(task, timeout=5)` | `result is True`; `refine_mock.assert_not_awaited()`; `send_album.await_count == 1`; `confirm_msg.edit_text` awaited con "Imagen final." como primer arg (vía `safe_edit_text`, sin reply_markup); `status_msg.delete.assert_awaited_once()` (delete_status default True); base_msgs `delete` NOT awaited | el branch falsy álbum no edita el confirm a "Imagen final." o le añade teclado |
| `test_send_comfyui_output_meta_none_skips_refine_choke` (d) | copiar L455-472 (bypass) pero sin video. Patch `_send_comfyui_image` AsyncMock `return_value=MagicMock()`; patch `_send_comfyui_confirm_refine` AsyncMock; `bot._pending_refine.clear()`. `ok = await bot._send_comfyui_output(_comfyui_model(), "/tmp/base.png", "prompt", status_msg, message, "Edit", _regen_ctx(uid))` — SIN `meta` (default None), SIN `cancel_event` | `ok is True`; `send_img.await_count == 1`; `confirm_refine.assert_not_awaited()`; `not bot._pending_refine`; `send_img.await_args.kwargs.get("reply_markup") == _image_regenerate_keyboard()` (default single) | el choke se activa con meta=None (regresión pre-ítem) |
| `test_album_batch_comfyui_chain_real_choke` (e) | copiar L1090-1113 (album routing) + L1116-1167 (chain). `sessions_file, generation_refs_file, monkeypatch`; `monkeypatch.setattr(bot, "COMFYUI_HOST", "1.2.3.4")`; uid=1102; comfyui config; `bot.user_state[uid] = {"model": "comfyui"}`; `anchor_msg = _album_message(user_id=uid, message_id=8, file_id="p1")`. Patch `_download_telegram_file_id` AsyncMock; `_fake_gen` → `(["/tmp/a.png"], None, dict(_COMFYUI_REMOTES))`; patch `_send_comfyui_image` AsyncMock `side_effect=[base1, refined1, base2]` (cada uno MagicMock con `delete`/`edit_reply_markup` AsyncMock); patch `_generate_comfyui_refine` AsyncMock → `(["/tmp/refined.png"], None)`; patch `process_image_result` AsyncMock. **NO mockear `_send_comfyui_output`.** `task = asyncio.create_task(bot._process_album_edit_from_file_ids(anchor_msg, "cambia el fondo", ["p1", "p2"]))`. Ítem 1: poll `_pending_refine` → `token1`; `await bot.handle_refine_decision(_refine_callback(uid, f"refine:{token1}:yes"))`. Ítem 2: poll hasta un token distinto (`any(t != token1 for t in bot._pending_refine)`); `await bot.handle_refine_decision(_refine_callback(uid, f"refine:{token2}:no"))`. `result = await asyncio.wait_for(task, timeout=10)` | `result is True`; `refine_mock.assert_awaited_once()`; `send_img.await_count == 3`; `mock_proc.assert_not_awaited()`; último `anchor_msg.reply.return_value.edit_text` contiene "Completadas 2/2"; `not bot._pending_refine` | la cadena álbum con choke real cuelga (await nunca resuelve) o no continúa tras la decisión |
| `test_variables_batch_comfyui_cancel_mid_chain_stops_clean` (f) | copiar L1116-1167 (chain) pero resolver con cancel. `sessions_file, variables_file, monkeypatch`; `monkeypatch.setattr(bot, "COMFYUI_HOST", "1.2.3.4")`; `bot.user_state[1001] = {"model": "comfyui"}`; `msg = _photo_message(caption="/variables 2")`; `msg.answer.return_value = _status_message()`. `_fake_gen` → `(["/tmp/v.png"], None, dict(_COMFYUI_REMOTES))`; patch `_send_comfyui_image` AsyncMock `side_effect=[base_msg]`; patch `_generate_comfyui_refine` AsyncMock; patch `variables_store.random_combination` → `("de pie, frontal, mirando", ("de pie", "frontal", "mirando"))`. `task = asyncio.create_task(bot._run_variables_batch(msg, 2, BytesIO(b"img"), None, source_file_id="p1"))`. Poll `_pending_refine` → token1. `job = next(j for j in bot._active_jobs[1001] if j["kind"] == "variables")`; `cb.data = f"cancel_job:{job['id']}"`, `cb.from_user.id = 1001`, `cb.answer = AsyncMock()`, `cb.message = msg.answer.return_value`. `await bot.handle_cancel_job(cb)`. `await asyncio.wait_for(task, timeout=10)` | `refine_mock.assert_not_awaited()`; `_fake_gen`/generate_image await UNA vez (ítem 1; el ítem 2 NO genera); último `msg.answer.return_value.edit_text` empieza con "⏹ Cancelado." y contiene "Completadas", y NO contiene "Listo:"; `1001 not in bot._active_jobs` (job finalizado); `not bot._pending_refine` | el cancel durante la cadena deja el loop colgado (wait_for timeout) o continúa al ítem 2 (generate_image 2 veces) o no reporta "Cancelado." |

Correr el harness y confirmar estado:

```bash
cd /home/ubuntu/repos/grok && ./venv/bin/python -m pytest tests/test_comfyui_refine.py -q 2>&1 | tail -12
```

- Si TODOS pasan → los branches diferidos son correctos (GREEN como guard de regresión).
- Si ALGUNO falla → analizar: si es un bug REAL en el branch (comportamiento contradictorio con el
  contrato del ítem), reportarlo en el resumen y arreglar en `bot.py` DENTRO del branch (scope
  acotado), re-correr hasta GREEN. NO expandir a otros branches.

**Verification:**

```bash
cd /home/ubuntu/repos/grok && ./venv/bin/python -m pytest tests/test_comfyui_refine.py -q
```

**Done:** Los tests (a)-(f) existen y pasan (35+ tests del harness). `bot.py` sin cambios salvo bug
expuesto y arreglado (documentado). Baseline de la suite capturado (435 passed, 2 skipped).

### Task 2: README + deploy note en `README.md`

**type:** auto
**Objective:** Documentar el flujo de confirmación de refino ComfyUI (Parte B) y la dependencia de
deploy del box (Parte C). Sin tests.
**Files:** `/home/ubuntu/repos/grok/README.md`
**Action:**

1. **Nueva sección "ComfyUI image editing (refine confirmation)"** después de la sección
   `## Batch image editing with variables (/variables)` (y antes de `## Environment variables`).
   Contenido exacto (inglés, copy del bot):
   - Cuando el modelo ComfyUI configurado tiene el refino habilitado (default), cada imagen
     generada pasa por un flujo en 2 etapas: primero se envía la **base** con los botones
     `[✨ Refinar][⏭ Continuar]`.
   - `✨ Refinar` re-refina la MISMA base (mismo modelo/prompt; usa el modo `REFINE_ONLY` del box) y
     publica la imagen refinada.
   - `⏭ Continuar` conserva la base como resultado final (la base pasa al teclado "Regenerar").
   - Si no hay decisión dentro del TTL (default **300 s**, env `REFINE_CONFIRM_TIMEOUT`) → la base
     es final.
   - Cancelar durante el refino (`Cancelar`) respeta la cadena en curso: en `/variables` (y en
     batch de álbum) el batch para limpio con "Cancelado. Completadas X/N" y la base se conserva.
   - **NO afirmar que los álbumes comfyui multi-foto funcionan**: `handle_album` no rutea grupos
     de medios comfyui (rama defensiva/dead del bot). El teclado de confirmación de imagen single
     va sobre la propia imagen.
2. **Tabla `## Environment variables`**: añadir fila `REFINE_CONFIRM_TIMEOUT` (No | TTL en segundos
   para la confirmación de refino (default 300); sin decisión → la base es final).
3. **Deploy note (Parte C)** — subsección "ComfyUI refine — deploy note (operator)" dentro de la
   sección ComfyUI:
   - El flujo de refino requiere el modo `REFINE_ONLY` en el box ComfyUI: `gen_comfy.py` actualizado
     (repo `comfyui-vast-setup`). Deploy MANUAL del operador (NO automático): `cp gen_comfy.py
     /workspace/gen_comfy.py` en el box. Sin ese deploy, el refino no está disponible (la base se
     conserva).

NO tocar el resto del README (secciones Models/Video//variables/Deployment/Tests intactas salvo la
fila de env y las 2 secciones nuevas).

**Verification:**

```bash
cd /home/ubuntu/repos/grok && git diff --stat README.md
```

**Done:** README con la sección ComfyUI (flujo + TTL + cancel), la fila `REFINE_CONFIRM_TIMEOUT` y
la deploy note. Sin afirmaciones de álbumes comfyui multi-foto. Resto del README intacto.

### Task 3: Regresión + commit work-unit

**type:** auto
**Objective:** La suite completa no introduce regresiones vs baseline de Task 1; commit único.
**Files:** `/home/ubuntu/repos/grok/tests/test_comfyui_refine.py`, `/home/ubuntu/repos/grok/README.md`
(+ `bot.py` si hubo fix acotado de bug)
**Action:**

Correr la suite completa y comparar con el baseline (435 passed, 2 skipped; no deben aparecer
fallos NUEVOS):

```bash
cd /home/ubuntu/repos/grok && ./venv/bin/python -m pytest tests -q 2>&1 | tail -3
```

Si aparece un fallo nuevo en un archivo que este ítem NO tocó, parar e investigar antes de commitear.

Commit único (work-unit) con los archivos del ítem (excluir `.grok/`, `.planning/`,
`variables_extract/` — untracked pre-existentes, no parte de este work-unit):

```bash
cd /home/ubuntu/repos/grok
git add tests/test_comfyui_refine.py README.md   # + bot.py solo si hubo fix acotado
git commit -m "feat(comfyui): cover refine deferral branches + README"
```

Sin `Co-Authored-By`. Verificar con `git status --short` y `git log --oneline -1`.

**Verification:**

```bash
cd /home/ubuntu/repos/grok && ./venv/bin/python -m pytest tests/test_comfyui_refine.py -q
cd /home/ubuntu/repos/grok && git status --short
cd /home/ubuntu/repos/grok && git log --oneline -1
```

**Done:** Harness verde. Suite completa sin fallos nuevos vs baseline. Commit único convencional, sin
`Co-Authored-By`; `git status` limpio (solo untracked pre-existentes).

## Instrucciones para gsd-executor

- **Orden de tasks es obligatorio:** Task 1 (tests) → Task 2 (README) → Task 3 (regresión + commit).
  No tocar README antes de tener los tests verdes.
- **Repo y runner:** cambio en `/home/ubuntu/repos/grok` (monolito aiogram). Runner:
  `cd /home/ubuntu/repos/grok && ./venv/bin/python -m pytest` (pytest 8.4.2, `asyncio_mode = auto`;
  NO hace falta `@pytest.mark.asyncio`). Baseline de la suite: 435 passed, 2 skipped (capturar al
  inicio de Task 1).
- **Skills a cargar ANTES de trabajar** (leer el SKILL.md completo de cada una):
  - `telegram-bot-hardener` — handlers aiogram + registry runtime + teclados; respetar reglas de
    handlers acotados y tests (pytest + pytest-asyncio/auto).
  - `test-quality-flow` — los tests deben validar comportamiento (los branches (a)-(f)), no
    documentar código roto.
  - `cognitive-doc-design` — README: flujo conciso, sin afirmaciones falsas (álbum multi-foto).
  - `work-unit-commits` — commit único work-unit, conventional, sin `Co-Authored-By`.
- **Patterns to copy (mecánico, no rediseñar):** poll `_pending_refine` L224-280; cancel vía
  `handle_cancel_job` L866-885; chain variables con choke REAL L1116-1167; setup álbum batch
  L1090-1113 — todos en `tests/test_comfyui_refine.py`.
- **Choke REAL obligatorio:** NO mockear `_send_comfyui_output`, `_send_comfyui_confirm_refine`,
  `handle_refine_decision`, `handle_cancel_job`, `_cancel_pending_refines_for_user`. Mock SOLO:
  `_generate_comfyui_refine`, `_send_comfyui_image`, `_send_comfyui_album`, `generate_image`,
  `_download_telegram_file_id`/`_download_telegram_photo`. TTL vía
  `monkeypatch.setattr(bot, "REFINE_CONFIRM_TIMEOUT", 0)`.
- **Gotchas (no pisar):**
  - `aioresponses` NO aplica (comfyui usa subprocess SSH, no HTTP). No usar HTTP mocks.
  - `bot._pending_refine.clear()` al inicio de cada test que toque el registry (autouse de conftest
    lo limpia al final, no al inicio).
  - El branch cancel (a) pone el teclado del base a `None` (NO `_image_regenerate_keyboard()`) y NO
    borra `status_msg` (el handler de cancel es dueño del status). No sobre-assert.
  - En (c), `safe_edit_text(confirm_msg, "Imagen final.")` → assert `confirm_msg.edit_text` con
    "Imagen final." como primer arg y SIN `reply_markup`.
  - En (e) cada foto del álbum es SINGLE (output len 1): el teclado de confirmación va en la imagen
    (`_send_comfyui_image` con `reply_markup=refine_kb`), NO hay confirm prompt separado; `message.answer`
    del choke solo se usa en el path álbum real. El status del álbum es `anchor_msg.reply.return_value`.
  - En (f), el job del batch está en `bot._active_jobs[uid]` con `kind == "variables"`; usar su
    `id` para `handle_cancel_job`. Tras el cancel, el ítem 2 NO debe generar (`generate_image` await
    solo 1 vez). El `completed` del resumen puede ser X≠0 (A6); NO fijar X exacto — assert
    "Cancelado." + "Completadas" y ausencia de "Listo:".
  - Para `_photo_message`/`_album_message`, respetar los helpers existentes; no crear nuevos salvo
    que el setup lo exija.
  - No mockear `os.path.exists`; los senders mockeados evitan archivos reales.
  - README: no afirmar que los álbumes comfyui multi-foto funcionan (dead branch `handle_album`).
  - El deploy del box (`cp gen_comfy.py /workspace/gen_comfy.py`) es acción MANUAL del operador: NO
    ejecutarlo, solo documentarlo.
- **No-touch verificable con `git diff --stat`:** la maquinaria del item 2 (choke, TTL,
  `_send_comfyui_confirm_refine`, teclados, registry), el wiring del item 3 (call sites,
  `_process_album_edit_from_file_ids`), `conftest.py`, `comfyui-vast-setup`.
- **Commit:** mensaje `feat(comfyui): cover refine deferral branches + README`, SIN `Co-Authored-By`.
  Un solo commit con `tests/test_comfyui_refine.py` + `README.md` (+ `bot.py` solo si hubo fix
  acotado). NO incluir `.grok/`, `.planning/`, `variables_extract/`.
- Si descubres gotchas no obvios o un bug expuesto, guárdalos en engram vía `mem_save` con
  `project: 'grok'` y `topic_key: 'architecture/comfyui-refine-confirm'`.

## Test commands

Harness (primario):

```bash
cd /home/ubuntu/repos/grok && ./venv/bin/python -m pytest tests/test_comfyui_refine.py -q
```

Regresión completa:

```bash
cd /home/ubuntu/repos/grok && ./venv/bin/python -m pytest tests -q
```

## Risks + Mitigation

| Risk (from impact) | Mitigation | Where |
|--------------------|------------|-------|
| MEDIUM — Tests diferidos mockean el choke (la "lógica de decisión/cancel") en vez del borde externo | Regla explícita: choke REAL; mock solo `_generate_comfyui_refine`/senders/`generate_image`; TTL vía env | Constraints; Instrucciones |
| MEDIUM — Un test (a)-(f) expone un bug real → tentación de expandir el fix | Fix acotado al branch del test, reportar, NO expandir (regla del pool) | Task 1; A1 |
| MEDIUM — Test de cadena (e)/(f) frágil por timing del poll de `_pending_refine` | Patrón ya probado (L1116-1167): loop `for _ in range(200): if _pending_refine: break; await sleep(0)` + `asyncio.wait_for(task, timeout=10)` | (e)/(f) |
| MEDIUM — README afirma álbumes comfyui multi-foto (falso: dead branch `handle_album`) | Instrucción explícita de NO afirmarlo; deploy note separada | Task 2 |
| LOW — El `completed` del resumen de cancel (f) no es 0 (A6) → assert sobre-pin falla | Assert de contrato: "Cancelado." + "Completadas", sin "Listo:"; no fijar X | A6; (f) |
| LOW — Commit incluye `.grok/`/`.planning/`/`variables_extract/` (untracked pre-existentes) | `git add` por nombre de archivo (solo tests + README + bot.py si aplica) | Task 3 |

## Success Criteria

- [ ] Branch (a): cancel con decisión resuelta → teclado del base a `None`, sin refino, `status_msg`
      no borrado por el choke, `_pending_refine` limpio, task `True`.
- [ ] Branch (b): `_generate_comfyui_refine` → `(None, err)` → single: base con regen kb + status con
      el error; álbum: `confirm_msg` borrado + base álbum conservada; task `True`.
- [ ] Branch (c): álbum + decisión `no` o TTL=0 → `confirm_msg` a "Imagen final." sin teclado;
      `status_msg` borrado; sin refino.
- [ ] Branch (d): `_send_comfyui_output` con `meta=None` → base final directa, sin confirm
      (comportamiento pre-ítem, sin regresión).
- [ ] Branch (e): álbum batch (2 fotos) con choke REAL → cadena por foto (base + decisión + refino),
      resumen "Completadas 2/2", sin hang.
- [ ] Branch (f): cancel durante la cadena de /variables → loop para limpio con "Cancelado.
      Completadas X/N", ítem 2 no genera, sin hang, job finalizado.
- [ ] README: sección ComfyUI (base → Refinar/Continuar → refino de la misma base; Continuar = base
      final; TTL 300s env `REFINE_CONFIRM_TIMEOUT`; cancel respeta la cadena) + fila de env +
      deploy note del `REFINE_ONLY`; NO afirma álbumes comfyui multi-foto.
- [ ] `bot.py` sin cambios salvo bug expuesto (documentado); maquinaria del item 2 y wiring del
      item 3 intactos (`git diff --stat`).
- [ ] Suite completa sin fallos NUEVOS vs baseline (435 passed, 2 skipped); commit único
      `feat(comfyui): cover refine deferral branches + README` sin `Co-Authored-By`; `git status`
      limpio (solo untracked pre-existentes).
