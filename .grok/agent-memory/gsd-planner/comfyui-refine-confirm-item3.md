# gsd-planner — comfyui-refine-confirm-item3

**Plan:** `.planning/quick/20260819-comfyui-refine-confirm-item3/PLAN.md` (autoritativo)
**Repo del cambio:** `/home/ubuntu/repos/grok` (bot.py; box NO se toca — ítem 1 cerrado)
**Impact cerrado:** `.grok/agent-memory/impact-analyzer/comfyui-refine-confirm-item3.md`
**Fecha:** 2026-08-19

## Qué planea este ítem

Wirear el choke de confirmación de refino (item 2) en los call sites restantes de
`_send_comfyui_output` + arreglar el gap del álbum. 5 cambios en `bot.py`:

1. **L1375 regen** (`handle_regenerate_image`) → `meta=kie_meta, cancel_event=cancel_event`.
2. **L1580 text-gen** (`_do_generate_text`) → `meta=kie_meta, cancel_event=None` (sin job).
3. **L2191 variables** (`_run_variables_batch`) → `meta=meta` (variable del loop, L2140),
   `cancel_event=cancel_event`. El await pausa el ítem; el loop continúa al siguiente (re-check
   `_job_cancelled` L2111/L2149). Cadena intacta.
4. **L2451 reply edit** (`handle_reply_edit`) → `meta=kie_meta, cancel_event=None` (sin job).
5. **Gap álbum** (`_process_album_edit_from_file_ids`, L1795-1917): hoy usa `process_image_result`
   (L1877, rompe para comfyui). Añadir rama comfyui → `_send_comfyui_output` con
   `meta=kie_meta, cancel_event=cancel_event, delete_status=False` (status "Editando i/N" reusado).
   Patrón a copiar: rama comfyui de `_process_single_photo_edit` L1739-1757.

## Decisiones locked (impact, cerradas)

- reply edit/text-gen NO inician job nuevo (`cancel_event=None` → pending entry job_id=None; TTL
  300s limpia; sin botón cancel). Sin scope expansion.
- `delete_status` intacto: True (default) en regen/text-gen/reply; False en variables/álbum.
- No re-abrir la maquinaria del item 2 (choke, TTL, B1/B2, teclados, `_pending_refine`, `_finish_job`).
- No tocar config/sessions/variables_flow/variables_store/gen_comfy.py.

## Files map

- Edit: `bot.py` (5 bloques: regen, text-gen, variables, reply, álbum); `tests/test_comfyui_refine.py`
  (append, 6 tests de cadena).
- No-touch: `_send_comfyui_output`, `_send_comfyui_confirm_refine`, `_generate_comfyui(_refine)`,
  teclados/registry item 2, `get_model`, `process_image_result`, `_process_single_photo_edit`,
  variables_flow/variables_store/sessions/config, gen_comfy.py.

## Runner

`cd /home/ubuntu/repos/grok && ./venv/bin/python -m pytest tests/test_comfyui_refine.py -q`
Regresión: `./venv/bin/python -m pytest tests -q` (baseline verificado: 428 passed, 2 skipped).

## Tests (Task 1, RED)

6 tests de cadena en `tests/test_comfyui_refine.py`: regen/text-gen/reply pasan meta (cancel_event
None en los sin job); variables pasa `meta=meta`+cancel_event y cadena continúa (chain end-to-end con
choke REAL: resolver ítem1 yes + ítem2 no → "Listo: 2/2"); álbum rutea comfyui por `_send_comfyui_output`
(no `process_image_result`) y termina "Completadas 2/2". Patrones de setup: `test_kie_provider.py`
L940/1430/1462/1494 (handlers) y `test_comfyui_refine.py` L222 (poll `_pending_refine`).

## Commit (work-unit)

Un commit convencional en grok con bot.py + tests/test_comfyui_refine.py:
`feat(comfyui): wire refine confirmation into batch chains` (sin Co-Authored-By).

## Skills / reglas para el executor

- TDD obligatorio: tests RED → impl → regresión + commit. No implementar primero.
- Skills: `telegram-bot-hardener`, `work-unit-commits`, `test-quality-flow`.
- Gotchas: variables usa `meta` (no `kie_meta`); álbum usa `anchor_message` + `delete_status=False`;
  `aioresponses` NO aplica a comfyui (subprocess SSH); chain test usa choke REAL (poll `_pending_refine`).
