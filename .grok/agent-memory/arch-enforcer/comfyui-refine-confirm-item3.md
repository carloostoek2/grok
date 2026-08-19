# Arch-Enforcer — comfyui-refine-confirm-item3

**Fecha:** 2026-08-19
**Commit auditado:** `378708d` `feat(comfyui): wire refine confirmation into batch chains`
**Veredicto:** PASS

## Alcance auditado

Ítem 3: wiring del choke de confirmación de refino ComfyUI en los 4 call sites restantes de
`_send_comfyui_output` + rama comfyui en `_process_album_edit_from_file_ids`.

## Verificaciones (todo conforme al PLAN)

1. **Call site regen** (`handle_regenerate_image` bot.py:1383-1384):
   `meta=kie_meta, cancel_event=cancel_event` ✓
2. **Call site text-gen** (`_do_generate_text` bot.py:1595-1596):
   `meta=kie_meta, cancel_event=None` ✓ (sin `_start_job`, locked)
3. **Call site variables** (`_run_variables_batch` bot.py:2225-2226):
   `meta=meta, cancel_event=cancel_event` ✓ (variable del loop, no `kie_meta`)
4. **Call site reply edit** (`handle_reply_edit` bot.py:2492-2493):
   `meta=kie_meta, cancel_event=None` ✓ (sin `_start_job`, locked)
5. **Rama álbum** (`_process_album_edit_from_file_ids` bot.py:1882-1900):
   comfyui rutea por `_send_comfyui_output` con `delete_status=False, meta=kie_meta,
   cancel_event=cancel_event`, `anchor_message` como message; NO usa `process_image_result`.
   Rama else conserva `process_image_result` (download_allowlist, kie_meta, regen_context) intacta.
   `completed += 1` (L1921) y `else:` del for con "Completadas N/N" (L1922-1926) intactos.
   Re-checks `_job_cancelled` (L1839/L1855/L1869) intactos.

## No-touch verificado

- `_send_comfyui_output` (bot.py:3609-3621): firma con `*` keyword-only `delete_status`, `meta`,
  `cancel_event` — SIN cambios en el commit (ya tenía meta/cancel_event del item 2).
- Maquinaria item 2 intacta: `_send_comfyui_confirm_refine` (L3714), `_register_pending_refine`
  (L713), `_refine_confirm_keyboard` (L691), `_finish_job` (L489), choke/TTL/B1/B2.
- No se iniciaron jobs nuevos en reply edit/text-gen (solo kwargs `meta`/`cancel_event`).
- `delete_status` intacto: True default en regen/text-gen/reply; False explícito en variables/álbum.
- config/sessions/variables_flow/variables_store/gen_comfy.py no tocados.
- Diff del commit: solo `bot.py` + `tests/test_comfyui_refine.py` (5 hunks de wiring + 6 tests).
  Sin scope creep.

## Tests

- Harness `tests/test_comfyui_refine.py`: **35 passed** (12+ item 2 + 6 nuevos item 3).
- Suite completa `tests/`: **434 passed, 2 skipped** (baseline 428 + 6 nuevos; sin fallos nuevos).
- `test_variables_command.py::test_batch_comfyui_uses_selected_model_and_sends_via_comfyui` sigue
  GREEN sin modificar (A6).
- Commit único convencional `feat(comfyui): wire refine confirmation into batch chains`, sin
  `Co-Authored-By`; `git status` sin modificaciones a archivos trackeados (solo untracked de
  pipeline: agent-memory/planning).

## Notes

Sin observaciones fuera del DoD. Residuo cero.
