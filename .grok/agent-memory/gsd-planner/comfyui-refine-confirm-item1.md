# gsd-planner — comfyui-refine-confirm-item1

**Plan:** `.planning/quick/20260819-comfyui-refine-confirm-item1/PLAN.md` (autoritativo)
**Repo del cambio:** `/home/ubuntu/comfyui-vast-setup` (NO grok — el bot es ítem 2)
**Impact cerrado:** `.grok/agent-memory/impact-analyzer/comfyui-refine-confirm-item1.md`
**Fecha:** 2026-08-19

## Qué planea este ítem

Modo `REFINE_ONLY` en `gen_comfy.py` (box ComfyUI): branch temprano en `main()`
(tras L188) que con `REFINE_ONLY` ∈ (1,true,yes) lee `REFINE_INPUT` (paths CSV) y
refina cada base existente con `run_refine` (mismas env REFINE_FACES/DENOISE/STEPS/CFG),
imprime paths refinados, exit 0/2/3. Base inexistente → skip + stderr (evita que
`run_refine` devuelva `[base_path]`); refino con excepción → conserva base (patrón
main L459-461). Ortogonal: flujo actual byte-idéntico si REFINE_ONLY ausente.

## Decisiones locked (reversibles)

- A7: branch tras L188, antes del comentario identity-edit (L190).
- A9: harness usa identity-edit (`MODEL=krea2 LORA=krea_edit` + INPUT_IMAGE temp) para
  el guard del flujo original — evita mockear `json.load` / payloads del box.
- A10: bases del harness = archivos temp reales; no se mockea `os.path.exists`.
- Timeout SSH 600s y quoting de REFINE_INPUT: NO se resuelven aquí (bot, ítem 2).

## Files map

- Edit: `gen_comfy.py` (docstring L2-13 + branch), `README.md` (sección Refinamiento L144-168)
- Create: `tests_refine_only.py` (harness, 6 tests, corre sin box)
- No-touch: `bot.py`, payloads/, workflows/, nodes/, scripts/, setup.sh

## Runner

`cd /home/ubuntu/comfyui-vast-setup && /home/ubuntu/repos/grok/venv/bin/python -m pytest tests_refine_only.py -q`
(pytest 8.4.2 en el venv; setup sin pytest.ini → defaults, tests síncronos).

## Commit (work-unit)

Un commit convencional en comfyui-vast-setup con gen_comfy.py + README.md +
tests_refine_only.py: `feat(gen_comfy): add REFINE_ONLY mode to refine pre-generated bases`
(sin Co-Authored-By).

## Skills / reglas para el executor

- Leer `AGENTS.md` del setup (REGLA #1: no degradar sin consultar).
- TDD obligatorio: tests RED → impl → docs + commit.
- Skills: `work-unit-commits`; ninguno extra (no toca bot).
