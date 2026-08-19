# gsd-planner — comfyui-refine-confirm-item4

**Plan:** `.planning/quick/20260819-comfyui-refine-confirm-item4/PLAN.md` (autoritativo)
**Repo del cambio:** `/home/ubuntu/repos/grok` (tests + README; box NO se toca — ítem 1 cerrado)
**Impact cerrado:** `.grok/agent-memory/impact-analyzer/comfyui-refine-confirm-item4.md`
**Fecha:** 2026-08-19

## Qué planea este ítem (cierre del pool)

Solo tests + docs. NO hay implementación nueva (items 2-3 cerrados). `bot.py` se toca ÚNICAMENTE si
un test diferido expone un bug real (scope acotado).

1. **Parte A — tests diferidos (T-S5 item 2 + residuales item 3)** en `tests/test_comfyui_refine.py`
   (append, choke REAL; mock solo bordes externos: `_generate_comfyui_refine`, senders,
   `generate_image`, TTL vía `REFINE_CONFIRM_TIMEOUT=0`). 7 funciones (8 casos, (c) parametrizada):
   - (a) cancel single → teclado a `None` (no regen), sin refino, status_msg no borrado por el choke
     (branch `decision is _REFINE_CANCELLED`, bot.py L3894-3906; force-resolve vía `handle_cancel_job`).
   - (b) refine-error single/álbum: `_generate_comfyui_refine` → `(None, err)` → base conservada con
     regen kb + status con error (single); `confirm_msg` borrado + base álbum conservada (álbum).
   - (c) álbum no/timeout → `confirm_msg` a "Imagen final." sin teclado + `status_msg` borrado.
   - (d) choke-negative: `_send_comfyui_output` con `meta=None` → base final directa (regresión
     pre-ítem).
   - (e) álbum batch (2 fotos) con choke REAL → cadena por foto (base + decisión + refino),
     resumen "Completadas 2/2". Cada foto es SINGLE (output len 1 → teclado en la imagen, sin
     confirm prompt separado).
   - (f) cancel mid-chain de /variables → loop para limpio "Cancelado. Completadas X/N", ítem 2 no
     genera, sin hang. Observado: `completed` suma 1 tras el ítem cancelado (choke devuelve True) →
     NO fijar X en el assert.
2. **Parte B — README del bot**: sección "ComfyUI image editing (refine confirmation)" (base →
   `[✨ Refinar][⏭ Continuar]` → refino de la MISMA base; Continuar = base final; TTL 300s env
   `REFINE_CONFIRM_TIMEOUT` → base final; cancel respeta la cadena de /variables) + fila de env
   `REFINE_CONFIRM_TIMEOUT`. NO afirmar álbumes comfyui multi-foto (dead branch `handle_album`).
3. **Parte C — deploy note**: el flujo requiere `REFINE_ONLY` en el box (`gen_comfy.py` actualizado,
   `cp gen_comfy.py /workspace/gen_comfy.py`, repo `comfyui-vast-setup`). Deploy MANUAL, no ejecutar.

## Files map

- Edit: `tests/test_comfyui_refine.py` (append, 7 tests); `README.md` (grok).
- Edit-si-bug: `bot.py` (solo bug expuesto por (a)-(f), scope acotado).
- No-touch: maquinaria item 2 (choke, TTL, `_send_comfyui_confirm_refine`, teclados, registry),
  wiring item 3 (call sites, `_process_album_edit_from_file_ids`), conftest.py, comfyui-vast-setup
  (deploy manual), config/sessions/variables_flow/variables_store.

## Runner

`cd /home/ubuntu/repos/grok && ./venv/bin/python -m pytest tests/test_comfyui_refine.py -q`
Regresión: `./venv/bin/python -m pytest tests -q` (baseline verificado: 435 passed, 2 skipped).

## Tasks (orden obligatorio)

1. Task 1 — tests (a)-(f) en `tests/test_comfyui_refine.py`, choke REAL. Se escriben ANTES del
   README. Deben pasar (GREEN); si uno falla → bug real → fix acotado → re-correr a GREEN.
2. Task 2 — README (sección ComfyUI + env + deploy note).
3. Task 3 — regresión completa vs baseline + commit único work-unit.

## Commit (work-unit)

Un commit convencional en grok: `feat(comfyui): cover refine deferral branches + README`
(sin Co-Authored-By). Archivos: `tests/test_comfyui_refine.py` + `README.md` (+ `bot.py` solo si hubo
fix acotado). NO incluir `.grok/`, `.planning/`, `variables_extract/`.

## Skills / reglas para el executor

- Orden obligatorio: Task 1 → Task 2 → Task 3. No tocar README antes de tests verdes.
- Skills: `telegram-bot-hardener`, `test-quality-flow`, `cognitive-doc-design`, `work-unit-commits`.
- Choke REAL: no mockear `_send_comfyui_output`/`_send_comfyui_confirm_refine`/`handle_refine_decision`/
  `handle_cancel_job`. Mock solo bordes externos.
- Gotchas: branch cancel (a) pone kb a `None` (no regen) y NO borra status; (c) assert
  `confirm_msg.edit_text("Imagen final.")` sin reply_markup; (e) cada foto es single; (f) job del
  batch en `_active_jobs[uid]` con `kind=="variables"`, no fijar X del resumen; `aioresponses` NO
  aplica (subprocess SSH); README sin afirmar álbum multi-foto.
