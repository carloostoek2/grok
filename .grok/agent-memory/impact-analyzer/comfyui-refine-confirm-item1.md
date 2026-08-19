# Impact Analysis: modo REFINE_ONLY en gen_comfy.py (box ComfyUI)

**Date:** 2026-08-19
**Change:** Branch temprano en `main()` de gen_comfy.py: env `REFINE_ONLY` + `REFINE_INPUT` (paths CSV) -> `run_refine` por path, print de paths refinados, exit 0/2/3. Sin tocar flujo actual.
**Analysis only** — no implementación.

## Executive Summary
Viable. El branch es ortogonal: se inserta en `main()` ANTES de la generación y solo se activa con `REFINE_ONLY`; el flujo actual (REFINE=1, txt2img, img2img, identity edit, video) queda intacto si el env está ausente. Riesgo global BAJO, con 3 riesgos reales a mitigar: (1) timeout SSH de 600s en `_comfyui_run_remote` vs cascade de refino `_run_graph(timeout=1200)` — multi-path secuencial puede exceder 600s; (2) `run_refine` devuelve `[base_path]` cuando el archivo NO existe, lo que confundiría "refinado" con "base sin refinar" en el flujo de confirmación; (3) quoting de REFINE_INPUT vía shell (riesgo bajo hoy, pero hardenable). Confirmado: `_generate_comfyui` (bot.py:3274) solo pasa `REFINE`, nunca `REFINE_ONLY`. No hay limpieza de /workspace/ComfyUI/output entre runs SSH. No hay tests del bot que toquen gen_comfy; el setup no tiene suite (proponer harness).

## Consumers / Call Sites
- bot.py:3304 y 3310-3313 — `_generate_comfyui` construye `MODEL='..' LORA='..' [REFINE='1'] python3 /workspace/gen_comfy.py`; `refine_env` solo para cr=="1" y cm!="wan_i2v". NO pasa REFINE_ONLY hoy. Confirmado.
- bot.py:3199-3216 — `_comfyui_run_remote`: filtro de stdout `startswith("/workspace")`, `returncode!=0 -> []`, timeout default 600.
- bot.py:3182-3196 — `_comfyui_ssh_base`: `ssh -p PORT -o BatchMode=yes -o ConnectTimeout=25 root@HOST`.
- gen.sh (scripts/comfyui-vast/gen.sh:27-28) — invoca gen_comfy.py con MODEL/LORA por SSH (usa tail -1).
- setup.sh:248 — deploy: `cp gen_comfy.py /workspace/gen_comfy.py` (nombre correcto; sin drift n.py).
- run_refine/gen_comfy.py:160-170, _run_graph:36-63, main:177-467.

## Risks
- MEDIUM — Timeout: SSH wrapper 600s (bot.py:3210) vs `_run_graph(timeout=1200)` en refine. Multi-path secuencial puede exceder 600s -> TimeoutExpired -> "Error de ComfyUI". Pre-existente en REFINE=1, pero REFINE_ONLY multiplica por N. Mitigar: cap en nº paths o timeout mayor en la llamada refine-only del bot.
- MEDIUM — Correctness: run_refine (163-164) retorna `[base_path]` si no existe; en REFINE_ONLY un base borrado pasaría como "refinado". Verificar existencia antes y emitir stderr/exit≠0.
- LOW — Shell quoting: `REFINE_INPUT='{paths}'`; paths vienen de stdout del propio script (filenames ComfyUI, solo `[\w.-]`), box ya es confiable. Harden con regex `^/workspace/ComfyUI/output/[\w./-]+$` en el bot.
- LOW — Degradación: REGLA #1 AGENTS.md — el branch no toca el flujo actual; mantener REFINE_FACES/DENOISE/STEPS/CFG idénticos (refine_steps=20 no el default 52 de run_refine).
- OK — Persistencia: sin rm/cleanup de output en setup.sh/scripts; paths sobreviven entre SSH (READ. "Los modelos/scripts/token PERSISTEN entre stop/start").
- OK — Secuencialidad: loop es secuencial, ComfyUI procesa una cola; sin race.

## Affected Tests
- Bot: NINGÚN test toca gen_comfy/_generate_comfyui/REFINE. Regresión: `venv/bin/python -m pytest tests/test_variables_command.py -q` (path comfyui batch, mockea generate_image) + suite completa `venv/bin/python -m pytest -q` (venv del repo, pytest.ini asyncio_mode=auto).
- Setup: sin suite. Proponer harness `tests_refine_only.py` (python stdlib o pytest): import gen_comfy (solo stdlib), monkeypatch `run_refine`/stdin/env, catch SystemExit, assert exit y stdout. Correr con venv del bot: `/home/ubuntu/repos/grok/venv/bin/python -m pytest`.

## Files Map
- Edit: /home/ubuntu/comfyui-vast-setup/gen_comfy.py (branch REFINE_ONLY + docstring cabecera)
- Edit: /home/ubuntu/comfyui-vast-setup/README.md (sección refino: modo refine-only)
- Create (sugerido): tests_refine_only.py en setup
- No touch: bot.py (item 1; bot solo en item 2), payloads/, workflows/, nodes/, setup.sh

## Ready for chain
Handoff a planner con scope tight: branch temprano, env REFINE_ONLY/REFINE_INPUT, exit codes, docstring+README, harness.
