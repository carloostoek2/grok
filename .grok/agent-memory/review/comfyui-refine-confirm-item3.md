# Review — comfyui-refine-confirm item 3 (wire choke into batch chains)

**Effort:** 3 (1 general + tests + plan) · **Rounds:** 3 · **Resultado:** 0 issues abiertos

## Commits (bot.py + tests)
- `378708d` — feat(comfyui): wire refine confirmation into batch chains
- `71d8beb` — fix(comfyui): avoid jobless cancel during refine in no-job flows
- `2afb4f1` — chore(comfyui): drop stale line ref in album comment

## Conteo por severidad
- Round 1: 1 bug · 1 suggestion · 4 nits (general 1+1+2; tests 0+2+2; plan 0+0+1)
  - Bug I3-B1: botón "Cancelar" jobless en reply/text-gen cancelaba un job no relacionado (fix: teclado cancel solo si `cancel_event is not None`).
- Fix round: 4 fixed + 3 wontfix (I3-N1 loops cuentan False como completado; I3-N2 test e2e álbum; I3-P1 desviación side_effect verificada necesaria).
- Round 2: 0 bugs · 1 nit nuevo (I3-N5 línea hardcodeada en comentario) → fix trivial (commit 2afb4f1).
- Round 3: 0 issues (todos CLEAN / APPROVE).

## Residuales clasificados
- `in-scope-followup` (ítem 4): (a) cancel-branch del flujo, (b) refine-failure branch, (c) album no/timeout → "Imagen final.", (d) choke-negative (meta=None); álbum con choke REAL per-item; cancel mid-chain batch-level.
- `out-of-scope`: extender `handle_album` para rutear álbumes comfyui (la rama comfyui del álbum es defensiva/dead hoy).
- `handoff`: deploy del box (ítem 1) pendiente.

## Handoff al documentador
- Harness: 36 tests en `tests/test_comfyui_refine.py` · Suite completa 435 passed + 2 skipped.
- La cadena de /variables con choke REAL (yes ítem 1 / no ítem 2 → continúa) está cubierta.
