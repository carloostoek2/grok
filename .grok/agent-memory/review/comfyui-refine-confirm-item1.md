# Review — comfyui-refine-confirm item 1 (REFINE_ONLY mode en gen_comfy.py)

**Effort:** 3 (1 general + tests + plan) · **Rounds:** 2 · **Resultado:** 0 issues abiertos

## Commits
- `384002c` — feat(gen_comfy): add REFINE_ONLY mode to refine pre-generated bases
- `1be1d9f` — fix(gen_comfy): harden REFINE_ONLY mode (image guard, isfile, input validation)

## Conteo por severidad
- Round 1: 0 bugs · 6 suggestions · 4 nits (general 2+2, tests 4+2)
- Fix round: 9 fixed + 1 wontfix (harness RED dependiente de /workspace/payloads — dev-runner-only por diseño)
- Round 2: 0 issues (todos los reviewers APPROVE / CLEAN)

## Residuales clasificados
- `out-of-scope`: timeout SSH 600s del bot vs `_run_graph(timeout=1200)` en refino multi-base → mitigado en ítem 2 (bot pasa timeout mayor).
- `out-of-scope`: shell quoting de REFINE_INPUT → hardenizado con regex en ítem 2 (bot).
- `handoff`: deploy del script al box (`cp gen_comfy.py /workspace/gen_comfy.py`) — manual del operador, requerido antes de usar el ítem 2.

## Handoff al documentador
- Harness: 16 tests en `comfyui-vast-setup/tests_refine_only.py`, SIN box.
- Regresión bot: 138 passed (test_variables_command + test_kie_provider).
