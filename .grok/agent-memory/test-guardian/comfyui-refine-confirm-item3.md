# test-guardian — comfyui-refine-confirm-item3 (veredicto)

**Ítem:** Bot — cadenas con pausa por ítem (wirear choke de confirmación de refino en regen/text-gen/variables/reply + rama comfyui en álbum)
**Commit auditado:** `378708d` (`feat(comfyui): wire refine confirmation into batch chains`)
**Plan autoritativo:** `.planning/quick/20260819-comfyui-refine-confirm-item3/PLAN.md` (gsd-planner)
**Fecha:** 2026-08-19

## Veredicto: SUITE PROTEGE ADECUADAMENTE

Sin mocks prohibidos. Los 6 tests del PLAN presentes y verdes (RED→GREEN). Contratos 1-5 cubiertos. Dos residuales FUERA de la tabla de tests del PLAN → clasificados, no inflan el ítem.

## Mock Audit — PASS (confianza ALTA en la cadena; MEDIA en routing álbum, cubierto por item 2)

Todos los mocks viven en bordes externos o inyectan el cableado a verificar; la lógica de decisión del ítem es REAL:

- **PERMITIDO (borde externo SSH/subprocess/generación):** `generate_image` (AsyncMock/side_effect con meta fija). El meta `comfyui_remotes` se inyecta para activar el choke; la generación real es item 1/2.
- **PERMITIDO (Telegram IO):** `_send_comfyui_image`, `_send_comfyui_album`, `_download_telegram_photo`, `_download_telegram_file_id`, mensajes MagicMock.
- **PERMITIDO (inyección de borde SSH):** `_generate_comfyui_refine` mockeado en la cadena — la decisión (llamado/omitido por ítem) es lo que se asserta; la ejecución SSH real la cubre item 2.
- **PERMITIDO (inyección de cableado):** `_send_comfyui_output` mockeado SOLO en los 4 tests de wiring (regen/text-gen/reply/variables-básico/álbum) donde el ASSERT ES el kwargs (meta/cancel_event/delete_status). El choke real interno es scope del item 2 (cubierto en `test_send_comfyui_output_*`).
- **PERMITIDO (colaborador de datos, no scope del ítem):** `variables_store.random_combination` patcheado a combo fijo.
- **PERMITIDO (assert de ruteo):** `process_image_result` mockeado en álbum y `assert_not_awaited()` — verifica la rama comfyui real no cae en el path KIE.
- **NO mockeado (lógica real verificada en la cadena):** `_send_comfyui_output` + `_send_comfyui_confirm_refine` + `_register_pending_refine` + `handle_refine_decision` corren REALES en `test_variables_batch_comfyui_chain_continues_after_decision` (poll `_pending_refine`, resuelve item1 yes / item2 no). La rama `if provider == comfyui` del álbum también es real.
- Sin `_mock_*_ctx`, sin mock de servicio/negocio.

## Cobertura de contratos — PLAN (5/5)

| Contrato | Test | Pasa |
|----------|------|------|
| 1. regen pasa meta y re-pausa (`cancel_event` real) | `test_regen_comfyui_passes_meta_and_cancel_event` | ✅ |
| 2. text-gen pasa meta con `cancel_event=None` | `test_text_gen_comfyui_passes_meta_cancel_none` | ✅ |
| 3. variables pasa `meta=meta`+`cancel_event` y cadena continúa | `test_variables_batch_comfyui_passes_meta_and_cancel_event` (+ `delete_status=False`, "Listo: 2/2") | ✅ |
| 4. reply pasa meta con `cancel_event=None` | `test_reply_edit_comfyui_passes_meta_cancel_none` | ✅ |
| 5. álbum comfyui rutea por `_send_comfyui_output` sin `process_image_result` | `test_album_batch_comfyui_routes_to_send_comfyui_output` (`mock_proc.assert_not_awaited`, "Completadas 2/2") | ✅ |

Cadena +6 tests (428→434). 

## Cadena con choke REAL + re-check `_job_cancelled` post-await

- **Variables yes/no end-to-end:** `test_variables_batch_comfyui_chain_continues_after_decision` corre el choke REAL: item1 yes → `_generate_comfyui_refine` llamada 1 vez; item2 no → no se llama; `send_img.await_count >= 3` (base, refinada, base); loop continúa → `_variables_batch_summary` "Listo: 2/2". ✅
- **Re-check `_job_cancelled` post-refino dentro del choke (B2, bot.py L3802):** cubierto por item 2 (`test_send_comfyui_output_confirm_yes_cancel_during_refine_keeps_base`). No es código nuevo del item 3.
- **Re-check por iteración del loop (L2136 variables / L1839 álbum):** pre-existente; cubierto por `test_variables_command.py` L614-619 ("Cancelado. Completadas 2/5") y `test_cancel_job.py`. El item 3 no cambia la semántica de cancel.

## GAPS reales (FUERA de la tabla de tests del PLAN — residuales, no inflar item 3)

1. **Cadena de álbum con choke REAL** (yes/no por ítem dentro del loop `_process_album_edit_from_file_ids`): `test_album_batch_comfyui_routes_to_send_comfyui_output` mockea `_send_comfyui_output`, así que verifica routing + continuación del loop pero NO la decisión per-item real dentro del álbum. La decisión real en álbum la cubre item 2 (`test_send_comfyui_output_confirm_yes_album`). Baja severidad — candidato al ítem 4.
2. **Cancel mid-chain comfyui en variables/álbum** (event.set() entre items): sin test nuevo en item 3. La mecánica es pre-existente y está cubierta (loop-cancel en `test_variables_command.py`/`test_cancel_job.py` + B2 en item 2). Baja severidad.

Ninguno está en la tabla de tests del PLAN ni bloquea el DoD (1)-(5). El ítem 4 (tests) puede absorberlos.

## Ejecución

- Harness: `./venv/bin/python -m pytest tests/test_comfyui_refine.py tests/test_variables_command.py tests/test_album_batch.py -q` → **95 passed**.
- Regresión: `./venv/bin/python -m pytest tests/ -q` → **434 passed, 2 skipped** (baseline 428+2 → +6).

## Acciones

Ninguna bloqueante. Proceder al ítem 4 (tests + README + deploy note); meter los GAPS 1-2 ahí. No se commitea nada en esta auditoría.
