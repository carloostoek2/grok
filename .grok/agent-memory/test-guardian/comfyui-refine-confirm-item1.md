# test-guardian — comfyui-refine-confirm-item1 (veredicto)

**Ítem:** Modo REFINE_ONLY en gen_comfy.py (box ComfyUI)
**Commit auditado:** `384002c` (gen_comfy.py docstring + branch L193-213, README, tests_refine_only.py)
**Plan autoritativo:** `.planning/quick/20260819-comfyui-refine-confirm-item1/PLAN.md`
**Fecha:** 2026-08-19

## Veredicto: SUITE PROTEGE ADECUADAMENTE

Sin mocks prohibidos. Cobertura de contratos completa. Sin GAPS bloqueantes.

## Mock Audit — PASS (confianza ALTA en el scope del ítem)

- **PERMITIDO (borde externo — box ComfyUI vía red):** `run_refine` mockeado en 5/6 tests
  y `_run_graph` en el guard del flujo original. `run_refine` es código PRE-existente
  (no tocado en el diff) que termina en HTTP al box — borde externo no disponible en CI.
  La PLAN lo sanciona explícitamente ("solo stdlib + monkeypatch de run_refine/_run_graph").
- **PERMITIDO (inyección de input CLI):** `sys.stdin` → `io.StringIO`.
- **NO mockeado (lógica real verificada):**
  - `os.path.exists(base)` — archivos temp REALES en `tmp_path` (`_make_base`); el
    chequeo de main() se ejercita de verdad.
  - split de `REFINE_INPUT` (CSV) — entrada real, split real en main().
  - exit codes (0/2/3) — `SystemExit` real vía `pytest.raises`.
  - stderr — `capsys` captura escritura real.
  - try/except de conservación de base — `fake_run` lanza `RuntimeError` real, lo
    atrapa el `except Exception` de main().
- Sin `MagicMock` de lógica de negocio, sin helpers `_mock_*_ctx`.

## Cobertura de contratos — COMPLETA (6/6 Truths del PLAN)

| Truth | Test | Pasa |
|-------|------|------|
| 1. Refina cada base existente, imprime refinados, exit 0 | `test_refine_only_refines_each_existing_base` | ✅ |
| 2. Base inexistente → no en stdout, run_refine NO se llama, stderr | `test_refine_only_skips_missing_base` + `..._all_missing_exits_3` (pytest.fail en run_refine) | ✅ |
| 3. Sin REFINE_INPUT → exit 2, stderr menciona REFINE_INPUT | `test_refine_only_without_input_exits_2` | ✅ |
| 4. Refino con excepción → base conservada, stderr | `test_refine_only_keeps_base_on_refine_error` | ✅ |
| 5. REFINE_ONLY ausente → flujo original intacto | `test_without_refine_only_keeps_refine1_flow` (identity-edit, `_run_graph` mock) | ✅ |
| 6. stdout solo líneas de paths | asserts `out.splitlines() == [paths...]` en 1, 2, 5, 6 | ✅ |

Además se verifica el paso de kwargs `{faces, denoise, steps, cfg}` desde env y el prompt
por stdin (test 1), y la precedencia de REFINE_ONLY sobre los modos de generación
(branch con exit temprano).

## Residuales (FUERA del DoD — clasificados, no inflar)

- **Multi-output por base** (`out += run_refine(...)`): la PLAN documenta que soporta
  varios outputs por base, pero ningún fake devuelve >1 path. Residual LOW; opcional un
  test barato (`fake_run` → 2 paths, assert ambos en stdout).
- **Variantes "true"/"yes"** de REFINE_ONLY: solo se prueba "1". Residual LOW (A1 define
  el set, el código lo implementa idéntico al patrón de `do_refine`).
- **Filtro de entradas vacías del CSV** (`if x.strip()`): no probado ("a,,b"). LOW.
- **REFINE_ONLY + krea2 (precedencia)**: el branch corta antes; no probado. LOW.

Ninguno está en los Truths del PLAN ni en el DoD del ítem.

## Ejecución

- Harness: `pytest tests_refine_only.py -q` → **6 passed** (0.02s, venv del proyecto).
- Regresión crítica del bot (no cambió, pero el pool depende de él):
  `pytest tests/test_variables_command.py tests/test_kie_provider.py -q` → **138 passed** (0.42s).

## Acciones

Ninguna. Harness y bot verdes; mocks solo en bordes externos; contratos completos.
El ítem 2 del pool (bot: generar → confirmar → refinar) puede proceder sobre esta base.
