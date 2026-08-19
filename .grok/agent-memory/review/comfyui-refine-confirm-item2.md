# Review — comfyui-refine-confirm item 2 (two-stage refine + confirmación interactiva)

**Effort:** 3 (1 general + tests + plan) · **Rounds:** 3 · **Resultado:** 0 issues abiertos

## Commits (bot.py + tests)
- `2ff0cfd` — feat(comfyui): two-stage refine with interactive confirmation
- `a1c9f0d` — fix(comfyui): cancel scoping + post-refine cancel + refine error mapping
- `f6f7f63` — fix(comfyui): surface refined-send failure + pin post-refine cancel test

## Conteo por severidad
- Round 1: 2 bugs · 11 suggestions · 7 nits
  - Bugs: B1 cancel fuerza-resolvía TODAS las decisiones del user (fix: filtrar por job_id); B2 cancel durante refino ignorado (fix: re-check post-refino).
- Fix round 1: 18 fixed + 2 wontfix (T-S5 ítem-4 pickup; P-N1 pipeline artifacts → documentador)
- Round 2: 0 bugs · 3 suggestion · 2 nit (nuevos: R2-1 B2 sin test, R2-2/R2-4 fallo de envío refinado, R2-3/R2-5)
- Fix round 2: 5 fixed (R2-1..R2-5)
- Round 3: 0 issues (todos APPROVE / CLEAN)

## Residuales clasificados
- `in-scope-followup` (ítem 4 del pool): (a) cancel-branch del flujo, (b) refine-failure branch, (c) album no/timeout → "Imagen final.", (d) choke-negative (meta=None).
- `out-of-scope`: artefactos `.grok/`/`.planning/` untracked → documentador al cierre.
- `handoff`: deploy del box (ítem 1) pendiente de ejecutar.

## Descubrimiento clave
- La Bot API rechaza botones con callback_data solo-texto sin callback handler? → se documentó en el log; se añadió handler `refine_noop` para el placeholder "Refinando…".

## Handoff al documentador
- Harness: 29 tests en `tests/test_comfyui_refine.py` · Suite completa 428 passed + 2 skipped.
- Los 4 call sites no-wired (1271/1476/2086/2346) + album flow se wirean en ítem 3.
