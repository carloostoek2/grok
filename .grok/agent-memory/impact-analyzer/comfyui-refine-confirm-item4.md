# Impact Analysis: comfyui-refine-confirm item 4 (tests diferidos + README + deploy note)

**Date:** 2026-08-19 · **Change:** completar cobertura de los branches diferidos (item 2 T-S5 + item 3 residuales), actualizar README del bot y nota de deploy del box. Análisis inline (contexto ya leído; subagente 402 en item 3).

## Consumers / Call Sites Map
- N/A — no cambia producción (solo tests + docs). Si un test diferido expone un bug, se arregla dentro del ítem (scope acotado).

## Scope
1. **Tests diferidos (item 2 T-S5 + item 3 residuales)** en `tests/test_comfyui_refine.py`:
   - (a) cancel-branch del flujo: cancel set + decisión → teclado a None, sin refino, status coherente.
   - (b) refine-failure branch: `_generate_comfyui_refine` devuelve `(None, err)` → base conservada con regen kb (single) / confirm_msg borrado (album) + status_msg con el error.
   - (c) album no/timeout → "Imagen final.": TTL=0 + decisión no → confirm_msg editado a "Imagen final." sin regen kb.
   - (d) choke-negative: `_send_comfyui_output` con meta=None → base final sin confirm (comportamiento pre-ítem).
   - álbum con choke REAL per-item: flujo de álbum con choke real (mock solo senders/generate) → cadena por foto (base + decisión + refino).
   - cancel mid-chain batch-level: cancel durante la cadena de variables → el loop para limpio (status "Cancelado. Completadas X/N").
2. **README del bot (grok)**: sección `/variables` y flujo comfyui → documentar la confirmación interactiva de refino (base → Refinar/Continuar → refino; TTL 300s → base final; cancel respeta la cadena).
3. **Deploy note**: gen_comfy.py `REFINE_ONLY` requiere deploy al box (`cp gen_comfy.py /workspace/gen_comfy.py`) antes de usar el flujo. Nota en README del bot o handoff al documentador.

## Risks
- Tests diferidos deben usar el choke REAL (mock solo bordes externos: `_generate_comfyui_refine`, senders, `generate_image`, TTL vía `REFINE_CONFIRM_TIMEOUT=0`). No mockear la lógica de decisión/cancel.
- README: no afirmar que los álbumes comfyui funcionan (rama defensiva/dead, handle_album no rutea comfyui).
- No tocar producción salvo bug expuesto por un test diferido.

## Affected Tests
- `tests/test_comfyui_refine.py` (append) · Regresión completa `./venv/bin/python -m pytest tests/ -q` (base 435+2).

## Files Map
- **Edit:** `tests/test_comfyui_refine.py`, `README.md` (grok).
- **No touch:** bot.py (salvo bug expuesto), comfyui-vast-setup (deploy note ya documentada en su README; el deploy es manual).

## DoD downstream
- Planner: tasks TDD por branch diferido + README + nota deploy.
- Executor: solo tests + docs; fix de producción SOLO si un test expone bug real.
- Arch: no-touch.
- Test-guardian: los branches diferidos ahora cubiertos con choke real.
