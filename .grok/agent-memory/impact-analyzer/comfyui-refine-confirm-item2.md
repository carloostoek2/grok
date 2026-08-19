# Impact Analysis: gen en 2 etapas + confirmación de refino (item 2 del pool)

**Date:** 2026-08-19
**Change:** `_generate_comfyui` base-only + `meta["comfyui_remotes"]`; nuevo `_generate_comfyui_refine` (REFINE_ONLY/REFINE_INPUT, timeout mayor); choke point en `_send_comfyui_output` con teclado `[✨ Refinar][⏭ Continuar]` + registry `_pending_refine` + future por decisión; cancel fuerza-resuelve; `_finish_job` limpia.
**Analysis only** — no implementación.

## Veredicto
**VIABLE con requisitos.** Los 5 call sites ya tienen `kie_meta`/`meta` en scope; ningún consumer asume shape exacta de `meta`; `_comfyui_run_remote`/`_comfyui_pull` tienen un solo caller. La concurrencia es SEGURA (aiogram 3.28.2 `handle_as_tasks=True`). Requisitos no negociables: cancel debe resolver los futures pendientes (si no, job slot cuelga), y ediciones de mensajes deben re-aplicar `reply_markup`.

## 1. Consumers / Call Sites

### `_send_comfyui_output` (L3412) — 5 call sites
| Línea | Handler | meta en scope | delete_status | Nota |
|---|---|---|---|---|
| L1271 | `handle_regenerate_image` | `kie_meta` | True (default) | pasar `meta=kie_meta` |
| L1476 | `_do_generate_text` | `kie_meta` | True | idem |
| L1637 | `_process_single_photo_edit` | `kie_meta` | True | hace `return await _send_comfyui_output(...)` |
| L2086 | `_run_variables_batch` | `meta` (loop) | **False** | el único que ya pasa `delete_status=False` |
| L2346 | `handle_reply_edit` | `kie_meta` | True | idem |

Las 5 llamadas son posicionales hasta `regen_context`; añadir `meta: dict | None = None` como kwarg tras `delete_status` no rompe ninguna. Todas ya tienen la meta en scope.

### `generate_image` / `_generate_once` — 7 call sites
L1255, L1467, L1622, L1752, L2035, L2054, L2333. Todas desempaquetan `(output, err, meta)`.
- `generate_image` L3121: `meta.get("retryable") is False` — solo se evalúa con err; la meta comfyui de éxito no tiene `retryable` → None → OK.
- Variables L2050/L2069: `meta.get("exhausted")` — SOLO cuando `err` es truthy. La meta `comfyui_remotes` es de éxito (err None) → nunca dispara. OK.
- `process_image_result` L3957-3960: `kie_meta.get("provider")`/`task_id` — comfyui nunca llega ahí: los 5 call sites derivan a `_send_comfyui_output`. Único matiz: album edit L1752 (fuera de los 5) llama `process_image_result` directo y NO deriva comfyui → gap PRE-EXISTENTE (comfyui + album edit ya falla hoy: paths locales → `_normalize_image_urls` vacío). No empeora con el cambio.

**Conclusión:** añadir `comfyui_remotes` a la meta de éxito no rompe NINGÚN consumer; todos usan `.get(...)` defensivo.

### `handle_regenerate_image` (L1190) / `_model_from_regen` (L695)
- `_model_from_regen` para comfyui → `get_model(user_id)` (L702) → reconstruye `comfyui_model`/`lora`/`refine` de sessions config (L840-842). **CONFIRMADO: `comfyui_refine` sí se reconstruye** → el choke point se re-dispara en regen (UX consistente).
- Contrato: handler await `_send_comfyui_output` y `finally: _finish_job` (L1301). Si el choke await una decisión, el finally se difiere y el slot queda ocupado hasta decidir → Cancel debe resolver (item 5). REQUISITO.

### `handle_cancel_job` (L1098) / `_finish_job` (L483)
- `handle_cancel_job` → `_request_cancel_job` (L465-480) setea `asyncio.Event` de los jobs del user. Hoy NO toca futures de `_pending_refine`. El plan (item 5) debe: (a) forzar-resolver los futures pendientes del user; (b) mantener el seteo de events (la cadena re-chequea `_job_cancelled` tras el await).
- `_finish_job` (L483) quita el event del registry; debe además barrer entradas `_pending_refine` huérfanas del user.

### `_comfyui_run_remote` (L3199) / `_comfyui_pull` (L3227)
- `_comfyui_run_remote(cmd, prompt, *, timeout=600)`: UN solo caller (`_generate_comfyui` L3305/L3314). Añadir `timeout` por-caller es seguro. OJO: `subprocess.run(timeout=...)` lanza `TimeoutExpired` NO capturado dentro; se propaga al `try/except` de `_generate_comfyui` → error genérico. Para refine N bases (≤1200s c/u) el timeout debe ser ≥ N×1200 o el error será críptico.
- `_comfyui_pull(remote_path)`: un solo caller. Inalterado.
- Exit codes: `_comfyui_run_remote` mapea returncode≠0 → `[]`. En REFINE_ONLY los exit 2/3 son significativos (base ausente / skip). El wrapper de refine debe distinguirlos de fallo real o reportar cuáles se saltaron.

## 2. Concurrencia — VEREDICTO con evidencia
**CONCURRENTE, sin deadlock.**
1. aiogram 3.28.2 (venv). `Dispatcher.start_polling` firma: `handle_as_tasks: bool = True`, `tasks_concurrency_limit=None`. `Dispatcher._polling` (source inspeccionado): por cada update `asyncio.create_task(handle_update)` SIN await; semáforo solo si `tasks_concurrency_limit` seteado (None aquí). => Cada update (message o callback_query) corre como task independiente en el MISMO event loop.
2. El código YA depende de esto: `handle_confirm_generation` (L1118) await inline `_do_generate_video` (L1171) / `_do_generate_text` (L1187) — operaciones largas — mientras el callback `cancel_job` (L1098) debe correr DURANTE ese await para cancelar. El retry loop hace `await asyncio.sleep(_retry_backoff)` (L3126) que cede el loop; `_update_retry_status` (L3825) re-aplica el teclado de cancel (fix de regression, L3833-3841) — solo tiene sentido si OTRA task (el callback) corre durante el sleep.
3. El patrón propuesto (handler A await future, handler B lo resuelve) es exactamente lo que ya ocurre entre el chain handler y el callback cancel/refine.
- Condición: la task que resuelve no debe depender de la bloqueada (los callbacks solo hacen `callback.answer` + resolver). Cumple.

## 3. Riesgos
- **MEDIUM — reply_markup**: editar sin `reply_markup` elimina el teclado (gotcha ya documentado en L3833 y en memoria). Cada edit en el choke point (base→final, Continuar→regen, swap) debe re-aplicar `reply_markup` vía `safe_edit_text`.
- **MEDIUM — Multi-imagen (álbum 5 bases)**: Telegram NO soporta inline keyboard en `sendMediaGroup`. El teclado Refinar/Continuar no puede ir en el álbum → necesita mensaje separado. Además el álbum final refinado tampoco lleva keyboard de regen (limitación pre-existente en `_send_comfyui_album`).
- **MEDIUM — Job slot ocupado durante await de decisión**: sin TTL, un usuario que nunca responde deja el slot ocupado y `_finish_job` no corre. Cancel (item 5) lo desbloquea; se recomienda además `asyncio.wait_for(future, timeout=...)` (p.ej. 300s) → timeout = "Continuar" (base final).
- **MEDIUM — Timeout SSH refine**: default 600s vs `_run_graph(timeout=1200)` por base; N=5 → 6000s. Pasar timeout acorde. `TimeoutExpired` no capturado → error genérico.
- **LOW — Regex paths** `^/workspace/ComfyUI/output/[\w./-]+$`: excluye espacios/`+`/`%`; el filtro actual (`startswith("/workspace")`) es más laxo. Validar contra filenames reales (subdirs temporales). Exit 2/3 significativos.
- **LOW — Meta shape**: sin consumers estrictos (todos `.get`). Seguro.
- **LOW — `_pending_refine` en memoria**: se pierde en reinicio; pero un reinicio mata la task del chain handler → sin hang post-reinicio. Relevante solo in-session; limpiar en cancel y `_finish_job`.
- **LOW — Idempotencia del callback**: tras resolver, un re-tap del botón stale (mismo token) no debe re-resolver. Validar token+user y no-op si ya resuelto.

## 4. Tests afectados
- `tests/test_variables_command.py:927` `test_batch_comfyui_uses_selected_model_and_sends_via_comfyui` — mockea `generate_image` (return `(["/tmp/comfyui_1.png"], None, None)`) y `_send_comfyui_output` (AsyncMock). Añadir kwarg `meta` NO rompe (AsyncMock acepta kwargs). Asserts: `mock_send.await_count == 2`, `delete_status is False`, `mock_proc.await_count == 0`.
- `tests/test_cancel_job.py` — `test_regenerate_respects_cancel` (L267) y `test_album_edit_stops_on_cancel` (L217) parchean `generate_image` con side_effect 3-tupla. `test_retry_status_preserves_cancel_keyboard` (L326) es el patrón del gotcha reply_markup.
- `tests/test_kie_provider.py:150` `test_generate_image_routes_to_kie` (unpack de `generate_image`); `test_handle_regenerate_image_text_mode` (L1430) y `test_handle_regenerate_image_edit_uses_kie_ref` (L1462) — regen con `generate_image` mockeado (no comfyui).
- `tests/test_round5.py:79,173` — `handle_confirm_generation` video path (aioresponses).
- ComfyUI real: NINGÚN test toca `_comfyui_run_remote`/`_comfyui_pull`/`_generate_comfyui`/`_send_comfyui_output` real. Patrón de mock = patch `generate_image` + AsyncMock de `_send_comfyui_output`. Para el refine nuevo, monkeypatch `_comfyui_run_remote`/`_comfyui_pull` directo (no aioresponses; es subprocess.run en thread).
- Comandos: `venv/bin/python -m pytest tests/test_variables_command.py -q` (trio comfyui); `venv/bin/python -m pytest tests/test_cancel_job.py tests/test_kie_provider.py tests/test_round5.py -q`; suite completa `venv/bin/python -m pytest -q` (venv del repo; pytest.ini `asyncio_mode = auto`).

## 5. Files Map + DoD downstream
- **bot.py** (edit): `_generate_comfyui` L3274 → base-only y devolver remotes (`(locals_, remotes, err)` o via meta); `_generate_once` L3130 rama comfyui → `meta={"comfyui_remotes": [...]}`; nuevo `_generate_comfyui_refine(model, prompt, remote_paths, *, status_msg=None)`; `_comfyui_run_remote` L3199 → param `timeout`; choke point en `_send_comfyui_output` L3412 + kwarg `meta`; registry `_pending_refine` + keyboard `[✨ Refinar][⏭ Continuar]`; handler `refine:<token>:<yes|no>` (valida token+user, idempotente); `handle_cancel_job` L1098 → fuerza-resolver futures; `_finish_job` L483 → barrer huérfanos.
- **tests** (create/update): refine flow (yes/no/cancel/timeout), cancel-during-pause, regex paths, timeout SSH.
- **DoD**: (1) test cancel-during-refine desbloquea el slot; (2) variables con `delete_status=False` intacto; (3) regen re-pausa (mismo keyboard); (4) gotcha reply_markup cubierto en cada edit del choke; (5) regex paths validada contra salidas reales; (6) timeout SSH ≥ N×1200s.
