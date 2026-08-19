# Impact Analysis: comfyui-refine-confirm item 3 (cadenas con pausa por ítem)

**Date:** 2026-08-19 · **Change:** wirear el choke de confirmación de refino (item 2) en los 4 call sites restantes de `_send_comfyui_output` + arreglar el gap de álbum.
**Analysis only** — no implementación. (El subagente impact-analyzer falló con 402; análisis inline del orquestador con contexto ya leído.)

## Consumers / Call Sites Map

| Call site | Función | meta en scope | cancel_event | delete_status | Estado |
|---|---|---|---|---|---|
| L1271 | `handle_regenerate_image` | `kie_meta` (L1255) | sí (L1213 `_start_job("regen")`) | default True | wire |
| L1476 | `_do_generate_text` | `kie_meta` (L1467) | NO (L1463 sin `_start_job`) | default True | wire (cancel_event=None) |
| L2086 | `_run_variables_batch` | `meta` (L2035) | sí (L1991 `_start_job("variables")`) | False (status reusado) | wire |
| L2346 | `handle_reply_edit` | `kie_meta` (L2333) | NO (L2330 sin `_start_job`) | default True | wire (cancel_event=None) |
| L1752 | `_process_album_edit_from_file_ids` | `kie_meta` (L1752) | sí (L1704 `_start_job("album_edit")`) | False (L1778) | GAP: usa `process_image_result`, no `_send_comfyui_output` |

## Risks

- **reply edit / text-gen sin job:** `cancel_event=None` → pending entry con `job_id=None`; el TTL (300s) limpia; no hay botón Cancel en status. Edge: un cancel de OTRO job (con job_id) NO resuelve el entry job_id=None (B1 filtra `entry["job_id"] != job_id` → lo ignora; el fallback all-for-user solo con `job_id is None`). Aceptable. Decisión: ¿iniciar job en esos flujos? → scope expansion, NO (mantener mínimo).
- **Loop de batch + await:** en variables/álbum, `_send_comfyui_output` await-ea la decisión dentro del ítem; el loop sigue tras el retorno (combo reuse, `completed += 1`). Re-check `_job_cancelled` en la siguiente iteración (L2006 variables / L1730 álbum). B1 (force-resolve por job) + B2 (re-check post-refino) ya cubren el cancel durante el await.
- **Álbum multi-foto + refino:** N fotos de entrada × (base + decisión + refino). El status "Editando i/N" se edita por ítem; `delete_status=False` mantiene el status.
- **Retry global de generate_image:** NO aplica al refino (paso aparte, item 2).
- **Gotcha reply_markup:** el choke (item 2) ya re-aplica teclados; no cambiar.
- **Álbum de salida multi-ángulo (5 imgs):** el choke ya maneja álbum (teclado en mensaje de texto separado).

## Affected Tests

- `tests/test_variables_command.py` (trio comfyui L927+): mocks `_send_comfyui_output` con `assert_awaited_once`/similar → añadir kwargs `meta`/`cancel_event` no rompe assert bare; verificar.
- `tests/test_album_batch.py`, `tests/test_cancel_job.py`, `tests/test_round5.py`, `tests/test_cmd_handlers.py`, `tests/test_long_prompt_collection.py`: revisar si llaman a los flujos wireados con comfyui.
- Nuevos: `tests/test_comfyui_refine.py` — añadir casos de cadena: variables (item acepta, item declina → continúa), álbum ruteo comfyui, regen/reply/text-gen con meta.
- Comandos: `./venv/bin/python -m pytest tests/test_variables_command.py tests/test_album_batch.py tests/test_cancel_job.py tests/test_comfyui_refine.py -q`; regresión completa `./venv/bin/python -m pytest tests/ -q` (base 428+2).

## Files Map
- **Edit:** bot.py (4 call sites + rama comfyui en álbum flow), tests/test_comfyui_refine.py (+ casos cadena), posiblemente tests/test_variables_command.py/test_album_batch.py (asserts).
- **No touch:** gen_comfy.py/comfyui-vast-setup, config_flow.py, sessions.py, variables_flow.py, variables_store.py, src/*.

## DoD downstream
- Planner: tasks tight por call site; decidir reply/text-gen (cancel_event=None) y el ruteo comfyui en álbum.
- Executor: no expandir scope (no iniciar jobs nuevos, no tocar config).
- Arch: no-touch.
- Test-guardian: cadena continúa tras aceptar/declinar/cancelar; álbum comfyui ruteado.
