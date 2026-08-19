# Arch-Enforcer — Audit ítem 1: Modo REFINE_ONLY en gen_comfy.py

- **Pool:** comfyui-refine-confirm
- **Ítem:** 1/4 (Box ComfyUI — REFINE_ONLY)
- **Commit auditado:** `384002c` (`feat(gen_comfy): add REFINE_ONLY mode to refine pre-generated bases`)
- **Fecha:** 2026-08-19

## Veredicto

**PASS** — sin violaciones. Harness 6/6 verde (venv grok, pytest 8.4.2).

## Verificaciones

1. **AGENTS.md REGLA #1 (CALIDAD ANTES QUE FACILIDAD):** sin degradación. El branch es ortogonal: se inserta tras `refine_cfg = float(...)` (L188) y antes del comentario `# --- Krea 2 Identity Edit` (L190), según el PLAN (A7). Flujo de generación (L190-467), identity-edit, video y defaults de refino intocados — el diff de `gen_comfy.py` solo muestra docstring (+3) y branch (+22).
2. **Contrato PLAN:** exit codes 0/2/3 correctos; `os.path.exists(base)` ANTES de `run_refine` (evita que base borrada pase como "refinada"); `try/except` conserva la base en salida (`out.append(base)`) con stderr `refino falló`; stdout del modo refine-only son SOLO lines de paths (`print(f)`), sin mensajes — compatible con el filtro `startswith("/workspace")` de `_comfyui_run_remote` (bot.py:3215).
3. **No-touch:** el commit toca solo `README.md`, `gen_comfy.py`, `tests_refine_only.py`. `payloads/`, `workflows/`, `nodes/`, `scripts/`, `setup.sh`, `docs/`, `inputs/` sin cambios. `git status` limpio.
4. **Flujo existente intacto:** `test_without_refine_only_keeps_refine1_flow` (regression guard identity-edit, REFINE=1, `REFINE_INPUT` ignorado) pasa. Llamada a `run_refine` con kwargs `faces/denoise/steps/cfg` coincide con su firma (L163).
5. **Commit:** único, convencional, sin `Co-Authored-By`; incluye tests + impl + docs (work-unit).

## Notes residuales (fuera del DoD del ítem)

- **out-of-scope:** timeout SSH 600s (bot.py:3210) vs `_run_graph(timeout=1200)` — documentado como MEDIUM, se mitiga en ítem 2 (según PLAN Non-goals). No es fallo de este ítem.
- **out-of-scope:** shell quoting de `REFINE_INPUT` — el bot hardeniza con regex en ítem 2 (PLAN Non-goals, impact LOW).

## Recomendación

Deploy al box (`cp gen_comfy.py /workspace/gen_comfy.py`) para el ítem 2. Sin blockers.
