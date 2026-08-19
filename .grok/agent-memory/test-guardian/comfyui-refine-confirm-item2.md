# test-guardian — comfyui-refine-confirm-item2 (veredicto)

**Ítem:** Bot core — generación en 2 etapas + confirmación interactiva de refino
**Commit auditado:** `2ff0cfd` (`feat(comfyui): two-stage refine with interactive confirmation`)
**Plan autoritativo:** `.planning/quick/20260819-comfyui-refine-confirm-item2/PLAN.md`
**Fecha:** 2026-08-19

## Veredicto: SUITE PROTEGE ADECUADAMENTE

Sin mocks prohibidos. Los 12 tests del PLAN presentes y verdes (RED→GREEN).
GAPS reales detectados, todos FUERA de la tabla de tests del PLAN → residuales clasificados, no inflar el ítem.

## Mock Audit — PASS (confianza ALTA en el scope del ítem)

Todos los mocks viven en bordes externos; la lógica de decisión del ítem es REAL:

- **PERMITIDO (SSH/subprocess):** `_comfyui_ssh_base`, `_comfyui_run_remote`, `_comfyui_pull`.
- **PERMITIDO (aiogram IO):** `_send_comfyui_image` / `_send_comfyui_album` (AsyncMock) y
  `message`/`status_msg`/`cb` MagicMock con métodos AsyncMock.
- **PERMITIDO (inyección de borde SSH en tests de flujo):** `_generate_comfyui_refine` mockeado
  en los tests yes/no/timeout/album — la decisión (llamado o no, con qué `comfyui_remotes`) es lo
  que se asserta; la ejecución SSH real se cubre aparte en `test_generate_comfyui_refine_runs_refine_only`.
- **NO mockeado (lógica real verificada):** `_send_comfyui_output` (choke A4) corre como task real;
  `_send_comfyui_confirm_refine` real; `handle_refine_decision` real (incl. idempotencia/ownership);
  TTL vía `asyncio.wait_for` real (monkeypatch `REFINE_CONFIRM_TIMEOUT=0`); force-resolve en
  `handle_cancel_job`/`_finish_job` real; regex de paths y construcción de cmd `REFINE_ONLY` reales.
- Sin `_mock_*_ctx`, sin mock de servicio/negocio.

## Cobertura de contratos — PLAN (asunciones A1-A11 + tabla 12 tests)

| Contrato | Test | Pasa |
|----------|------|------|
| Base-only, sin `REFINE=`, meta `comfyui_remotes` (A3) | `test_generate_comfyui_base_only_no_refine_env` | ✅ |
| Refino `REFINE_ONLY=1` + `REFINE_INPUT`, sin `INPUT_IMAGE`, timeout ≥1200 (A9) | `test_generate_comfyui_refine_runs_refine_only` | ✅ |
| Fail-closed paths inválidos (A10) | `test_generate_comfyui_refine_rejects_invalid_paths` + `test_refine_remote_path_validation` (7 negativos + 1 positivo) | ✅ |
| Choke se ACTIVA con las condiciones (A4 positivo) | tests yes/no/timeout/album (real `_send_comfyui_output`) | ✅ |
| yes → refina y borra base single (A6/A8) | `test_..._confirm_yes_single` | ✅ |
| no/timeout → base final + swap a regen kb (A6) | `test_..._confirm_no_keeps_base` + `..._timeout_finalizes` | ✅ |
| cancel fuerza-resuelve, slot no cuelga (DoD 1, mecanismo) | `test_handle_cancel_job_resolves_pending_refine` + `test_finish_job_cleans_pending_refine` | ✅ |
| TTL → base final (300s env, 0 en test) | `test_..._confirm_timeout_finalizes` | ✅ |
| Álbum: teclado en texto separado, base conservada (A7 yes) | `test_..._confirm_yes_album` | ✅ |
| Idempotencia token stale/re-tap (sin InvalidStateError) | `test_refine_decision_stale_and_retap_idempotent` | ✅ |
| Ownership (user ajeno ignorado) | `test_refine_decision_wrong_user_ignored` | ✅ |

## GAPS reales (FUERA de la tabla del PLAN — residuales, no inflar ítem 2)

1. **Branch cancel en el flujo** (`_send_comfyui_confirm_refine` L3753-3765): ningún test corre el
   flujo con el cancel event seteado. El re-chequeo `_job_cancelled` (L3708) y la limpieza
   single (`edit_reply_markup(None)`) / álbum (`confirm_msg.delete`) quedan como código muerto en
   tests. DoD(1) cubre solo el mecanismo (future resuelto), no la rama de flujo.
   Acción recomendada: test de flujo completo (task + `handle_cancel_job`/`event.set()` + assert limpieza).
2. **Refino falla → notificación + base final** (L3716-3729): `rerr` → `status_msg.edit_text` +
   base→regen kb (single) / `confirm_msg.delete` (álbum). Código nuevo del ítem sin proteger.
   Acción recomendada: `refine_mock` devolviendo `(None, "err")` + assert notificación y teclado.
3. **Álbum no/timeout** (L3768-3773): swap `safe_edit_text(confirm_msg, "Imagen final.")` sin regen
   kb (A7) — el álbum solo prueba yes.
4. **Choke negativo (A3/A4):** `_send_comfyui_output` con `meta=None` o `comfyui_refine≠1` → base
   final, choke inactivo. Sin test; protege los 4 call sites no-wired y el regen (los cubre el ítem 3).
5. **Triviales:** fallback `base_msg is None` (L3697); default TTL 300 no asserteado; fórmula de
   timeout multi-base (N>1) no probada (solo N=1).

Ninguno está en la tabla de tests del PLAN ni bloquea el DoD (1)-(6). El ítem 4 (tests) puede absorberlos.

## Ejecución

- Harness: `pytest tests/test_comfyui_refine.py -q` → **12 passed** (0.04s, venv del proyecto).
- Regresión: `pytest tests/test_variables_command.py tests/test_cancel_job.py tests/test_kie_provider.py tests/test_round5.py -q` → **165 passed** (0.47s).

## Acciones

Ninguna bloqueante. Proceder al ítem 3 (wirear call sites 1271/1476/2086/2346 + album-flow) y
meter los GAPS 1-3 en el ítem 4 de tests. No se commitea nada en esta auditoría.
