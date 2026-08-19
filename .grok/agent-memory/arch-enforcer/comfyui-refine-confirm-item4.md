# arch-enforcer — comfyui-refine-confirm-item4

- **Fecha:** 2026-08-19
- **Commit auditado:** `05c70c0` (HEAD, `feat(comfyui): cover refine deferral branches + README`)
- **Veredicto:** PASS WITH NOTES

## Alcance del commit

Solo 2 archivos: `tests/test_comfyui_refine.py` (+385) y `README.md` (+16). `bot.py` SIN cambios
(machinery item 2 y wiring item 3 intactos; `git diff HEAD --stat -- bot.py` vacío). No toca
`comfyui-vast-setup`; no deploy (Parte C = solo documentación). Worktree limpio de cambios trackeados
(solo untracked pre-existentes `.grok/`, `.planning/`, `variables_extract/`).

## Tests (7 funciones, 8 casos)

Harness `tests/test_comfyui_refine.py`: 44 passed (8 nuevos de este ítem). Suite completa:
443 passed, 2 skipped = baseline 435 + 8, SIN fallos nuevos.

| Branch | Test | Verdict |
|--------|------|---------|
| (a) cancel single | `test_send_comfyui_output_confirm_cancel_removes_keyboard` | PASS — kb base a None, refine NOT awaited, status no borrado, `_pending_refine` vacío |
| (b) refine-error single | `test_send_comfyui_output_confirm_refine_error_single_keeps_base` | PASS — base con regen kb, status con error, base.delete NOT awaited |
| (b) refine-error álbum | `test_send_comfyui_output_confirm_refine_error_album_keeps_base` | PASS — confirm borrado, base álbum conservada, error en status |
| (c) álbum no/timeout | `test_send_comfyui_output_confirm_album_final_image[no\|timeout]` | PASS — "Imagen final." sin reply_markup, status borrado, sin refino |
| (d) meta=None bypass | `test_send_comfyui_output_meta_none_skips_refine_choke` | PASS — skip confirm, base directa, `_pending_refine` vacío |
| (e) álbum chain choke real | `test_album_batch_comfyui_chain_real_choke` | PASS — refine await 1, `_send_comfyui_image` 3, "Completadas 2/2", sin hang |
| (f) cancel mid-chain | `test_variables_batch_comfyui_cancel_mid_chain_stops_clean` | PASS — "⏹ Cancelado." + "Completadas", sin "Listo:", item 2 no genera, job finalizado |

Choke REAL en a/b/c/e/f: no se mockean `_send_comfyui_output`, `handle_refine_decision`,
`handle_cancel_job`; mock solo bordes externos (`_generate_comfyui_refine`, senders, `generate_image`,
`_download_telegram_file_id`, `process_image_result`, `variables_store.random_combination`).

## README

Flujo base → `[✨ Refinar][⏭ Continuar]` → refino de la MISMA base (REFINE_ONLY); Continuar = base
final (teclado Regenerar); TTL default 300 s env `REFINE_CONFIRM_TIMEOUT`; cancel respeta la cadena
("Cancelado. Completadas X/N"); NO afirma álbum comfyui multi-foto (declara explícitamente que
`handle_album` NO rutea media groups — dead branch); deploy note manual
(`cp gen_comfy.py /workspace/gen_comfy.py`, sin deploy automático). Fila `REFINE_CONFIRM_TIMEOUT`
añadida a la tabla de env. Sección colocada entre `/variables` y `## Environment variables`.

## NOTES (fuera de DoD, no bloquean)

1. **(d) mockea el choke** — `test_send_comfyui_output_meta_none_skips_refine_choke` patchea
   `_send_comfyui_confirm_refine` (contradice la regla literal "NO mockear `_send_comfyui_confirm_refine`").
   Justificado: es el test de BYPASS (verificar NOT-call exige mock) y la propia tabla Task 1 del PLAN
   instruye `patch ... confirm_refine; assert_not_awaited()`. No faked pass: además guarda
   `_pending_refine` vacío + `_send_comfyui_image` una vez. Benigno.
2. **(d) assert reply_markup distinto al spec** — el plan esperaba
   `send_img.await_args.kwargs.get("reply_markup") == _image_regenerate_keyboard()`; el test aserta
   `"reply_markup" not in send_img.await_args.kwargs`. Verificado en bot.py L3655-3658 + L3550: en el
   path meta=None el caller NO pasa reply_markup y el kb regen se aplica DENTRO de `_send_comfyui_image`
   (`kb = _image_regenerate_keyboard() if reply_markup is None else reply_markup`). El assert del test es
   MÁS correcto que el del plan (el spec asumía mal dónde se aplica el kb). Benigno.

## No-touch verificado

- Maquinaria item 2 (choke, TTL, `_send_comfyui_confirm_refine`, teclados, registry `_pending_refine`): intacta.
- Wiring item 3 (call sites, `_process_album_edit_from_file_ids`): intacto.
- `conftest.py`, `comfyui-vast-setup`: no tocados.
- Commit único convencional, sin `Co-Authored-By`, mensaje `feat(comfyui): cover refine deferral branches + README`.
