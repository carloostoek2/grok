# Arch-Enforcer — Audit ítem 2: generación en 2 etapas + confirmación interactiva de refino

- **Pool:** comfyui-refine-confirm
- **Ítem:** 2/4 (Bot core — maquinaria de pausa interactiva)
- **Commit auditado:** `2ff0cfd` (`feat(comfyui): two-stage refine with interactive confirmation`)
- **Fecha:** 2026-08-19

## Veredicto

**PASS** — sin violaciones al DoD. Harness 12/12 verde; suite completa 411 passed, 2 skipped (sin fallos nuevos).

## Verificaciones

1. **No-touch:** commit toca solo `bot.py`, `tests/conftest.py`, `tests/test_comfyui_refine.py`. Los 4 call sites no-wired de `_send_comfyui_output` (L1346 regen, L1551 text-gen, L2162 variables, L2422 reply) NO pasan `meta`/`cancel_event` → choke inactivo → envían base (estado intermedio A3/A4). `_model_from_regen`, `get_model`, `_comfyui_is_video`, `_process_album_edit_from_file_ids`, `process_image_result`, `_generate_kie_once` sin cambios en el diff. Album flow, variables, config, gen_comfy.py intocados.
2. **Choke:** se activa SOLO con `comfyui_refine=="1"` + `meta` no None + `comfyui_remotes` no vacío, y el branch video corre primero. Sin meta → comportamiento actual. Meta propagado de verdad: `generate_image` → `_generate_once` → `_generate_comfyui` devuelve `{"comfyui_remotes": remotes}` y el wire L1711 lo pasa como `meta=kie_meta`.
3. **Base-only:** `_generate_comfyui` eliminó `refine_env` (sin `REFINE=`) y devuelve 3-tupla; refino vive en `_generate_comfyui_refine` (REFINE_ONLY=1 + REFINE_INPUT validado con regex `^/workspace/[A-Za-z0-9_./-]{1,300}$`, fail-closed; timeout `1200*N+300`).
4. **Cancelación:** `handle_cancel_job` fuerza-resuelve con `_cancel_pending_refines_for_user`; `_finish_job` limpia huérfanos (resuelve a False + pop); `_send_comfyui_confirm_refine` re-chequea `_job_cancelled` tras el await (`_REFINE_CANCELLED` gana sobre yes/no).
5. **TTL:** `asyncio.wait_for(future, REFINE_CONFIRM_TIMEOUT)` default 300s (env-configurable) → base final si no decide.
6. **Gotchas:** single yes borra la base; álbum conserva base + teclado en mensaje de texto separado (A7); swap de teclado vía `edit_reply_markup` (no edit_text) en single; "Imagen final." vía `safe_edit_text` sin teclado regen en álbum; `save_ref=True` en la base (A8).
7. **Timeout SSH:** `_comfyui_run_remote` captura `subprocess.TimeoutExpired` → devuelve `[]`, no lanza.
8. **Commit:** único, convencional, sin `Co-Authored-By`; conftest añade `bot._pending_refine.clear()` (1 línea).

## Notes residuales (fuera del DoD del ítem)

- **mejora sobre PLAN:** en el yes-single, la impl unwraps `refined[0]` antes de `_send_comfyui_image` (`single_refined = refined[0] if isinstance(refined, list) else refined`). El bloque 13 del PLAN pasaba la lista cruda → `open(str(list))` habría fallado. La impl corrige un bug latente del plan.
- **out-of-scope:** en el branch álbum no se chequea el retorno de `_send_comfyui_album` base (None → confirm_msg aún se envía). Coincide con el PLAN; probabilidad ínfima (archivos recién bajados). Ítem 3 puede endurecer.
- **out-of-scope:** en el error de refino `status_msg.edit_text(rerr, reply_markup=None)` suelta el teclado cancel del status_msg — según PLAN bloque 13; inofensivo (job termina tras return). Inconsistencia menor con el gotcha de re-aplicar reply_markup.

## Recomendación

Proceder al ítem 3 (wirear call sites 1271/1476/2086/2346 + album-flow). Sin blockers.
