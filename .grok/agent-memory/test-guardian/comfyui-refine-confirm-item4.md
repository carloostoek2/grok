# Test-Guardian — Item 4 (último) pool "comfyui-refine-confirm"

Commit auditado: `05c70c0` (pure test+docs: `tests/test_comfyui_refine.py` +385, `README.md` +16). Sin cambios de producción.

## Veredicto
**SUITE PROTEGE ADECUADAMENTE** — no hay mocks prohibidos; veredicto positivo. No se commiteó nada.

## Mock Audit (mock-audit.md)
Branches (a)-(f) usan el choke REAL: `_send_comfyui_output`, `_send_comfyui_confirm_refine`, `handle_refine_decision` y `handle_cancel_job` NO se mockean (salvo (d), ver abajo). Solo se mockean bordes externos:
- `_send_comfyui_image` / `_send_comfyui_album` — envío Telegram (borde permitido).
- `_generate_comfyui_refine` — SSH al box ComfyUI (borde permitido).
- `generate_image`, `_download_telegram_file_id`, `variables_store.random_combination` — generación/lista externa.

### (d) `test_send_comfyui_output_meta_none_skips_refine_choke` — mock de `_send_comfyui_confirm_refine` LEGÍTIMO
El arch-enforcer lo marcó. Es el test de bypass (meta=None → la condición `meta is not None and bool(meta.get("comfyui_remotes"))` falla y el choke ni se entra). El mock se usa como **spy** para `assert_not_awaited()`, NO como sustituto de lógica: el comportamiento del choke se verifica con el choke real en (a),(b),(c),(e),(f). No da falsa confianza. PERMITIDO.

## Cobertura (asserts de comportamiento, no tautologías)
- (a) cancel force-resolve: keyboard→None (NO regen), refine no await, base/status no delete, pending limpio. Verifica distinción cancel vs final.
- (b) refine-error single/álbum: base restaurada a `_image_regenerate_keyboard()` (comparado al real), confirm_msg borrado, error en status, base no borrada.
- (c) álbum no/timeout → `confirm_msg.edit_text == "Imagen final."` (exacto, sin reply_markup), status borrado, refine no await. Timeout real con `REFINE_CONFIRM_TIMEOUT=0`.
- (d) gate bypass: send_img una vez, confirm_refine no await, sin reply_markup, pending vacío.
- (e) chain álbum con choke real: refine una vez (item1 yes), 3 sends (base1/refined1/base2), "Completadas 2/2", pending vacío.
- (f) cancel mid-chain variables: `gen_calls == 1` (item2 NUNCA genera), "⏹ Cancelado. Completadas", sin "Listo:", job fuera de `_active_jobs`, pending vacío.

Path single "no/timeout → regen" ya cubierto por tests pre-existentes (líneas 324-376, 614-682). Éxito/fallo de entrega de álbum refinado ya cubierto en tests previos (líneas 401/509/540). Rama multi-foto `_send_comfyui_confirm_refine` con álbum real NO es ruteada (handle_album la descarta) — rama defensiva documentada.

## README
Refleja el comportamiento real. Keyboard `[✨ Refinar][⏭ Continuar]` coincide con `_refine_confirm_keyboard`. NO afirma álbum multi-foto — explícitamente dice "ComfyUI multi-photo albums are NOT routed". Documenta `REFINE_CONFIRM_TIMEOUT` (default 300s). La nota "album batch" del cancel se refiere al batch de edición multi-foto, correcto.

## Tests
- `tests/test_comfyui_refine.py`: 44 passed.
- Suite completa `tests/`: 443 passed, 2 skipped.

## Residuales (FUERA del DoD, clasificados)
1. (a) deja el job del uid 6021 colgado en `_active_jobs` (nunca `_finish_job`). Inofensivo por uids únicos; higiene menor.
2. (d) mock-spy del choke: patrón correcto pero depende de que (a)-(f) cubran el choke real para no dar falsa confianza. Documentado, no requiere acción.
