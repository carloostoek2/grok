# gsd-planner — comfyui-refine-confirm-item2

**Plan:** `.planning/quick/20260819-comfyui-refine-confirm-item2/PLAN.md` (autoritativo)
**Repo del cambio:** `/home/ubuntu/repos/grok` (bot.py; el box NO se toca — ítem 1 cerrado)
**Impact cerrado:** `.grok/agent-memory/impact-analyzer/comfyui-refine-confirm-item2.md`
**Fecha:** 2026-08-19

## Qué planea este ítem

Maquinaria en `bot.py` para la pausa interactiva de refino ComfyUI:
1. `_generate_comfyui` (L3274) → base-only (sin `REFINE=`) y devuelve `meta["comfyui_remotes"]`
   (paths remotos); `_generate_once` (L3130) comfyui y `generate_image` propagan el meta.
2. Nuevo `_generate_comfyui_refine(model, prompt, remote_paths, *, status_msg=None)`:
   `REFINE_ONLY='1' REFINE_INPUT='<CSV validado>'` (regex `^/workspace/[A-Za-z0-9_./-]{1,300}$`,
   fail-closed), timeout escalado `1200*N+300` (el box usa 1200s POR base, gen_comfy.py L173),
   `_comfyui_run_remote` captura `subprocess.TimeoutExpired`.
3. Confirmación: teclado `[✨ Refinar][⏭ Continuar]` (`refine:<token>:<yes|no>`), registry
   `_pending_refine[token] = {future, user_id, message_id, job_id}` con `asyncio.Future`;
   `handle_refine_decision` valida token+user y es idempotente ante re-tap/stale.
4. TTL: `asyncio.wait_for(future, REFINE_CONFIRM_TIMEOUT)` (env, default 300s) → no decide =
   base final. Evita slots ocupados.
5. Choke `_send_comfyui_output` (L3412) con `meta=None, cancel_event=None`: si
   `comfyui_refine=="1"` + meta con remotes + no video → `_send_comfyui_confirm_refine`:
   yes → refina (single borra base, álbum conserva base + álbum refinado nuevo); no/timeout →
   base final (single swap a `_image_regenerate_keyboard()`; álbum: msg de confirmación → "Imagen final.").
6. Cancelación: `handle_cancel_job` fuerza-resuelve a `_REFINE_CANCELLED`; el flujo re-chequea
   `_job_cancelled`; `_finish_job` limpia huérfanos.
7. Wire solo `_process_single_photo_edit` (L1637): `meta=kie_meta, cancel_event=cancel_event`.

## Decisiones locked (reversibles, planner)

- A7: álbum → teclado de confirmación en mensaje de TEXTO separado; en no/timeout ese msg →
  "Imagen final." SIN regen kb (el handler regen exige `callback.message.photo`, L1192). El swap a
  regen kb solo en single.
- A8: base guarda `generation_ref` (`save_ref=True`) → el botón Regenerar funciona tras no/timeout.
- A9: timeout refino = `1200*N + 300`.
- A10: validación de paths remotos con regex + fail-closed.
- A11: `_pending_refine` se limpia en `conftest.py::reset_runtime_state` (1 línea).
- Refactor contenido: `_send_comfyui_image`/`_send_comfyui_album` (único caller:
  `_send_comfyui_output`) pasan a `reply_markup`/`save_ref` configurables y devuelven el mensaje.

## Files map

- Edit: `bot.py` (14 bloques: constantes/registry, keyboard+helpers, handler refine,
  cancel force-resolve, `_finish_job`, `_comfyui_run_remote`, `_generate_comfyui` base-only+meta,
  `_generate_once`, `_generate_comfyui_refine`+validador, `_send_comfyui_image`,
  `_send_comfyui_album`+`_build_comfyui_album_media`, `_send_comfyui_output`, `_send_comfyui_confirm_refine`,
  `_process_single_photo_edit`); `tests/conftest.py` (1 línea).
- Create: `tests/test_comfyui_refine.py` (12 tests, TDD: RED Task 1 → GREEN Task 2/3).
- No-touch: 4 call sites no-wired de `_send_comfyui_output` (1271/1476/2086/2346 — ítem 3),
  `_process_album_edit_from_file_ids`, variables, `_model_from_regen`, `get_model`,
  `_comfyui_is_video`, `process_image_result`, `comfyui-vast-setup` (sin deploy).

## Runner

`cd /home/ubuntu/repos/grok && ./venv/bin/python -m pytest tests/test_comfyui_refine.py -q`
(pytest 8.4.2, `asyncio_mode = auto`). Regresión: `./venv/bin/python -m pytest tests -q`
(vs baseline capturado al inicio de Task 1).

## Commit (work-unit)

Un commit convencional en grok con bot.py + tests/test_comfyui_refine.py + tests/conftest.py:
`feat(comfyui): two-stage refine with interactive confirmation` (sin Co-Authored-By).

## Skills / reglas para el executor

- TDD obligatorio: tests RED → impl → regresión + commit. No implementar primero.
- Skills: `telegram-bot-hardener`, `work-unit-commits`, `test-quality-flow`.
- Gotchas: editar sin `reply_markup` elimina teclado (usar `edit_reply_markup` explícito /
  `safe_edit_text`); el refino NO pasa por `GENERATE_MAX_RETRIES` (error handling propio);
  `aioresponses` NO aplica a comfyui (subprocess SSH, no HTTP).
