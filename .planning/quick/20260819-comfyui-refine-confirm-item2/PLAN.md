---
phase: quick
plan: comfyui-refine-confirm-item2
type: auto
item: Bot — generación en 2 etapas + confirmación interactiva de refino
source: pool comfyui-refine-confirm
mode: standard
impact_ref: .grok/agent-memory/impact-analyzer/comfyui-refine-confirm-item2.md
test_command: cd /home/ubuntu/repos/grok && ./venv/bin/python -m pytest tests/test_comfyui_refine.py -q
---

## Objective

El bot (aiogram 3, `bot.py`) gana la **maquinaria de pausa interactiva** que cierra
el flujo ComfyUI en 2 etapas: `_generate_comfyui` genera SOLO la base (sin cascada)
y expone los paths remotos en `meta["comfyui_remotes"]`; un nuevo `_generate_comfyui_refine`
refina las bases YA generadas vía `REFINE_ONLY=1` + `REFINE_INPUT=<CSV>` con timeout
escalado; y el choke `_send_comfyui_output` (cuando `comfyui_refine=="1"` y no es video)
envía base + teclado `[✨ Refinar][⏭ Continuar]` (callback `refine:<token>:<yes|no>`),
await-ea la decisión con TTL (`REFINE_CONFIRM_TIMEOUT`, default 300s) y, según el
resultado, refina (yes → refinado final; single borra la base) o deja la base como final
(no/timeout → swap a teclado de regenerar). Cancelación fuerza-resuelve el future; el
flujo re-chequea `_job_cancelled` tras el await. Scope acotado al ítem 2: solo se wirea
`_process_single_photo_edit` (L1637); los otros 4 call sites de `_send_comfyui_output`
(1271/1476/2086/2346) y el album-flow los toca el ítem 3.

Este ítem NO desplega al box (`gen_comfy.py` ya tiene `REFINE_ONLY` — ítem 1 cerrado).

## Scope

- **In:**
  - `bot.py`: constantes + registry `_pending_refine` + `handle_refine_decision` +
    `_generate_comfyui_refine` + `_validate_refine_remote_path` + `_send_comfyui_confirm_refine` +
    base-only en `_generate_comfyui` (meta con `comfyui_remotes`) + params `meta`/`cancel_event`
    en `_send_comfyui_output` + refactor mínimo de `_send_comfyui_image`/`_send_comfyui_album`
    (`reply_markup`/`save_ref` configurables, devuelven el mensaje) + `_build_comfyui_album_media` +
    harden de `_comfyui_run_remote` (Timeouter) + force-resolve en `handle_cancel_job` +
    cleanup huérfano en `_finish_job` + wire `_process_single_photo_edit` (L1637).
  - `tests/test_comfyui_refine.py` (nuevo, harness TDD) + `tests/conftest.py` (1 línea).
- **Out / Non-goals:**
  - Los otros 4 call sites de `_send_comfyui_output` (L1271 regen, L1476 text-gen, L2086
    variables, L2346 reply): NO se wirean aquí — siguen sin `meta` → el choke NO se activa →
    envían la base sin refinar (estado intermedio aceptado, los wirea el ítem 3).
  - `_process_album_edit_from_file_ids` (L1752): NO deriva a `_send_comfyui_output` (gap
    pre-existente; lo arregla el ítem 3). No empeora.
  - Album flow / variables / config: no-touch.
  - `gen_comfy.py` (box) / `comfyui-vast-setup`: no-touch — NO desplegar.
  - Retry global de `generate_image` (GENERATE_MAX_RETRIES=5): NO aplica al refino (paso
    aparte, error handling propio en `_generate_comfyui_refine`).
- **Constraints:**
  - No tocar los 4 call sites no-wired (1271/1476/2086/2346) salvo que YA pasen meta (no).
  - `_send_comfyui_image`/`_send_comfyui_album` tienen UN solo caller (`_send_comfyui_output`)
    → el refactor de firma es contenido; otros helpers (L3973/4002) NO se tocan.
  - Concurrencia SEGURA: aiogram 3.28.2 `handle_as_tasks=True` → handlers como tasks
    independientes; el patrón handler-await-future + callback-resuelve es el flujo actual
    (confirm L1118, retry con cancel). Verificado por impact-analyzer — no reabrir.
  - Gotcha del repo: editar sin `reply_markup` ELIMINA el teclado — usar `safe_edit_text`
    o re-aplicar `reply_markup` explícito en cada edit del flujo.
  - `aioresponses` NO aplica a comfyui (usa subprocess SSH, no HTTP).

## Assumptions

Cerradas por impact-analyzer — no reabrir:

- **A1:** El refino NO pasa por el retry de `generate_image`; `_generate_comfyui_refine` es
  un paso aparte con su propio `try/except` (devuelve `(None, err)`).
- **A2:** `_model_from_regen` (L695-703) reconstruye el modelo con `comfyui_refine` (vía
  `get_model`, L842) → el choke re-dispararía en regen SI el call site pasara meta. Como
  regen NO se wirea en este ítem (meta=None → choke inactivo), no hay regresión; el ítem 3
  lo activa.
- **A3:** Base-only es global: `_generate_comfyui` deja de pasar `REFINE='{cr}'`. El box
  `do_refine` es False si `REFINE` ausente (gen_comfy.py L186-187) → genera base. Los 4 call
  sites no-wired mandan base sin refinar hasta el ítem 3 (estado intermedio aceptado).
- **A4:** El choke se activa SOLO si `model["comfyui_refine"]=="1"` Y `meta` trae
  `comfyui_remotes` no vacío Y no es video (`_comfyui_is_video`). Sin meta → comportamiento
  actual (base final).
- **A5:** Video (wan_i2v) nunca tuvo refino (`refine_env` era "" para wan_i2v) → base-only y
  el choke lo dejan intacto (el branch video corre primero en `_send_comfyui_output`).
- **A6:** El teclado de confirmación de la base se reusa para el resultado final cuando el
  usuario NO quiere refinar: se SWAPEA por `_image_regenerate_keyboard()` via
  `edit_reply_markup` (no `edit_text` — es un mensaje con foto).
- **A7 (decisión planner):** Para ÁLBUM (multi-imagen, batch de 5), el teclado va en un
  mensaje de TEXTO separado; en "no/timeout" ese mensaje se edita a "Imagen final." (SIN
  `_image_regenerate_keyboard()`: un teclado `regen` sobre texto rompe — el handler regen
  exige `callback.message.photo`, L1192). El álbum base se conserva siempre. `_image_regenerate_keyboard()`
  se swap-ea solo en el caso single (donde la base es un mensaje con foto).
- **A8 (decisión planner):** `_send_comfyui_base_single`/álbum guardan `generation_ref`
  (`save_ref=True`) → tras "no/timeout" el botón Regenerar del single funciona
  (evita regresión del regen). En "yes" la base single se borra (ref huérfana, inofensiva).
- **A9 (decisión planner):** El timeout del refino se escala POR BASE: el box usa
  `_run_graph(timeout=1200)` por refino (gen_comfy.py L173); N bases → `1200*N + 300`s.
- **A10 (decisión planner):** Paths remotos del box se validan con regex
  `^/workspace/[A-Za-z0-9_./-]{1,300}$` antes de embeberlos en el shell (defensa LOW contra
  `'`/`;`/`&`/`|`/`` ` ``/`$`/espacio/newline). Path inválido → fail-closed (no corre).
- **A11:** `_pending_refine` es runtime-state (como `_active_jobs`); `conftest.py` lo limpia
  en el fixture `reset_runtime_state` (1 línea) para no filtrar futures entre tests.

## Architecture Approach

### QUÉ (behavior / contracts)

**Outcome:** con comfyui activo y `comfyui_refine="1"`, una generación no-video manda la
base + teclado de confirmación, y según la decisión del usuario (o el TTL) el bot refina la
MISMA base en el box o la deja como resultado final. La base single se borra cuando el
usuario refina; el álbum base se conserva y el refinado sale como álbum nuevo.

**Happy path (single, yes)**

1. `_process_single_photo_edit` → `generate_image` → `_generate_comfyui` (base-only) →
   `_generate_once` devuelve `(locals, None, {"comfyui_remotes": [...5 paths...]})`.
2. `_send_comfyui_output(model, output, ..., meta=kie_meta, cancel_event=...)` → choke
   activo → `_send_comfyui_confirm_refine`.
3. Se registra `_pending_refine[token]` con un `asyncio.Future`; se envía la base (foto)
   con el teclado `[✨ Refinar][⏭ Continuar]`.
4. `asyncio.wait_for(future, timeout=REFINE_CONFIRM_TIMEOUT)`.
5. Usuario toca ✨ → `handle_refine_decision` resuelve `future.set_result(True)`.
6. `_generate_comfyui_refine(model, prompt, remotes)` → `REFINE_ONLY=1` + `REFINE_INPUT`
   (CSV de remotes validados) con timeout `1200*N+300` → pull → locals.
7. Se envía el refinado con `_image_regenerate_keyboard()` + `generation_ref`; se borra la
   base. `status_msg` se elimina. Job termina (`_finish_job`).

**Decisiones alternas**

| Decisión | Qué pasa | UI |
|----------|----------|-----|
| yes | refinar → mandar refinado (regen kb + gen_ref); single borra la base; álbum conserva la base y manda álbum refinado nuevo; msg de confirmación (álbum) se borra | base reemplazada (single) / álbum nuevo (album) |
| no | base es final | swap teclado a `_image_regenerate_keyboard()` (single) / msg de confirmación → "Imagen final." (álbum); `status_msg` eliminado |
| timeout (TTL) | base es final (mismo que no) + aviso | igual que no |
| cancel (`cancel_job`) | `handle_cancel_job` fuerza-resuelve el future a `_REFINE_CANCELLED`; el flujo re-chequea `_job_cancelled` y no refina | single: teclado a None; álbum: msg de confirmación borrado; `status_msg` ya dice "⏹ Cancelando…" |
| refino FALLA | base queda como resultado final + notifica el error | `status_msg.edit_text(rerr)`; single: teclado a regen; álbum: msg de confirmación borrado |

**Truths (must be true at the end)**

1. `_generate_comfyui` no pasa `REFINE=` al box y devuelve `meta["comfyui_remotes"]` = paths
   remotos; `_generate_once` comfyui devuelve ese meta; `generate_image` lo propaga.
2. `_generate_comfyui_refine` corre `REFINE_ONLY='1' REFINE_INPUT='<CSV>'` (sin
   `INPUT_IMAGE=`), con timeout >= `1200*N`, y devuelve los locales del refinado.
3. Paths de refino inválidos (meta-char/espacio/fuera de /workspace) → `_generate_comfyui_refine`
   devuelve error SIN correr el remoto (fail-closed).
4. Choke: single con refine="1" → base con teclado confirm; yes → refinado enviado + base
   borrada; no/timeout → base final con teclado `_image_regenerate_keyboard()`.
5. Álbum: base álbum conservada; teclado de confirmación en mensaje de texto separado;
   yes → álbum refinado nuevo (y msg de confirmación borrado); no/timeout → msg → "Imagen final.".
6. `handle_refine_decision` valida token+user; re-tap de token stale/resuelto = no-op
   idempotente (no crashea, no doble-resuelve).
7. `handle_cancel_job` fuerza-resuelve los futures pendientes del user a `_REFINE_CANCELLED`;
   el flujo tras el await re-chequea `_job_cancelled` y no refina.
8. `_finish_job` limpia entradas huérfanas de `_pending_refine` de ese user+job.
9. `_comfyui_run_remote` captura `subprocess.TimeoutExpired` (devuelve `[]`, no lanza).
10. Video (wan_i2v) y call sites sin meta: comportamiento actual intacto.

### CÓMO (structure / patterns)

- **Layer:** todo en `bot.py` (monolito aiogram). Sin módulos nuevos.
- **Pattern to copy:**
  - `handle_confirm_generation` L1118-1188 — callback handler con `@dp.callback_query` +
    filtro lambda `startswith`, valida estado y edita el mensaje.
  - `_image_regenerate_keyboard` L667-673 — builder de `InlineKeyboardMarkup` con callback_data.
  - `_start_job`/`_request_cancel_job` L446-480 — runtime registry `_active_jobs` (misma
    forma para `_pending_refine`) + `event.job_id`.
  - `_cancel_job_keyboard` L440-443 — builder con un botón y callback_data.
  - `_send_comfyui_image`/`_send_comfyui_album` L3332-3489 — enviar foto/álbum + guardar
    `generation_ref` + `_image_regenerate_keyboard()`.
  - `safe_edit_text` L851-873 — editar sin crashear en "message is not modified".
  - try/except silencioso para edit/delete de mensajes (patrón `handle_cancel_job` L1106-1113).
- **Interfaces / types:** `meta` shape libre (`dict | None`), consumido con `.get()` — ningún
  consumer asume shape estricta. `_send_comfyui_image` pasa a devolver `types.Message | None`;
  `_send_comfyui_album` a `list[types.Message] | None` (callers actualizados en el mismo
  archivo, único caller: `_send_comfyui_output`).

### Exact implementation

#### 1. Constantes + registry (insertar tras `GENERATE_MAX_RETRIES = 5`, L170)

```python
GENERATE_MAX_RETRIES = 5
# --- Item 2 (comfyui refine confirm): confirmación interactiva de refino ---
REFINE_CONFIRM_TIMEOUT = int(os.environ.get("REFINE_CONFIRM_TIMEOUT", "300"))  # segundos
REFINE_REFINE_TIMEOUT_PER_BASE = 1200  # el box usa _run_graph(timeout=1200) POR base (gen_comfy.py:173)
_REFINE_CANCELLED = object()  # sentinel: future resuelto por cancelación
_pending_refine: dict[str, dict] = {}  # token -> {future, user_id, message_id, job_id}
_REFINE_REMOTE_PATH_RE = re.compile(r"^/workspace/[A-Za-z0-9_./-]{1,300}$")
```

#### 2. Keyboard + registry helpers (insertar tras `_image_regenerate_keyboard`, L667-673)

```python
def _refine_confirm_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✨ Refinar", callback_data=f"refine:{token}:yes"),
                InlineKeyboardButton(text="⏭ Continuar", callback_data=f"refine:{token}:no"),
            ],
        ]
    )


def _register_pending_refine(
    token: str, *, user_id: int, message_id: int, job_id: str | None
) -> asyncio.Future:
    future = asyncio.get_running_loop().create_future()
    _pending_refine[token] = {
        "future": future,
        "user_id": user_id,
        "message_id": message_id,
        "job_id": job_id,
    }
    return future


def _drop_pending_refine(token: str) -> None:
    _pending_refine.pop(token, None)


def _cancel_pending_refines_for_user(user_id: int) -> None:
    for token in list(_pending_refine):
        entry = _pending_refine[token]
        if entry["user_id"] == user_id and not entry["future"].done():
            entry["future"].set_result(_REFINE_CANCELLED)
```

#### 3. Handler de decisión (insertar DESPUÉS de `handle_confirm_generation`, tras L1188)

```python
@dp.callback_query(lambda c: bool(c.data) and c.data.startswith("refine:"))
async def handle_refine_decision(callback: types.CallbackQuery):
    """Resuelve la decisión de refino (refine:<token>:yes|no). Re-tap de un token
    ya resuelto/borrado = no-op idempotente (answer informativo)."""
    parts = (callback.data or "").split(":")
    if len(parts) != 3:
        await callback.answer("Acción inválida.")
        return
    token, choice = parts[1], parts[2]
    entry = _pending_refine.get(token)
    if entry is None or entry["future"].done():
        await callback.answer("La confirmación ya se procesó.", show_alert=True)
        return
    if entry["user_id"] != callback.from_user.id:
        await callback.answer("No es tu confirmación.", show_alert=True)
        return
    if choice == "yes":
        entry["future"].set_result(True)
        await callback.answer("Refinando…")
    else:
        entry["future"].set_result(False)
        await callback.answer("Listo, imagen final.")
```

#### 4. `handle_cancel_job` (L1098-1115) — fuerza-resolver pendientes

Dentro del `if _request_cancel_job(...)` exitoso, ANTES de `await callback.answer("Cancelando…")`:

```python
    if _request_cancel_job(callback.from_user.id, job_id=job_id):
        _cancel_pending_refines_for_user(callback.from_user.id)
        await callback.answer("Cancelando…")
```

#### 5. `_finish_job` (L483-494) — limpiar huérfanos

```python
def _finish_job(user_id: int, event: asyncio.Event | None) -> None:
    if event is None:
        return
    jobs = _active_jobs.get(user_id)
    if not jobs:
        return
    remaining = [job for job in jobs if job["event"] is not event]
    if remaining:
        _active_jobs[user_id] = remaining
    else:
        _active_jobs.pop(user_id, None)
    # Item 2: limpiar confirmaciones de refino huérfanas de este user+job.
    job_id = getattr(event, "job_id", None)
    for token in list(_pending_refine):
        entry = _pending_refine[token]
        if entry["user_id"] == user_id and (job_id is None or entry["job_id"] == job_id):
            if not entry["future"].done():
                entry["future"].set_result(False)
            _pending_refine.pop(token, None)
```

#### 6. `_comfyui_run_remote` (L3199-3216) — capturar TimeoutExpired

Envolver el `asyncio.to_thread(subprocess.run, ...)`:

```python
    try:
        proc = await asyncio.to_thread(
            subprocess.run,
            ssh_base.split() + [cmd],
            input=prompt.encode(),
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        print(f"[comfyui] run remoto excedió el timeout de {timeout}s")
        return []
```

#### 7. `_generate_comfyui` (L3274-3329) — base-only + meta de remotes

Reemplazo COMPLETO de la función (firma pasa a devolver 3-tupla):

```python
async def _generate_comfyui(
    model: dict,
    prompt: str,
    image_data: BytesIO | None = None,
    *,
    status_msg: types.Message | None = None,
) -> tuple[list[str] | None, str | None, dict | None]:
    """Generate (txt2img) or edit (img2img) via ComfyUI on the Vast box.

    Item 2: genera SOLO la base (sin cascada de refino). Los paths remotos se
    devuelven en meta["comfyui_remotes"] para que el flujo de confirmación
    (_send_comfyui_confirm_refine) pueda refinarlos en una segunda pasada.
    Returns (lista de local_paths, err, meta)."""
    _ssh_base, port, err = _comfyui_ssh_base()
    if err:
        return None, err, None
    cm = model.get("comfyui_model", "krea2")
    cl = model.get("comfyui_lora", "none")
    try:
        if image_data is None:
            if cm == "wan_i2v":
                return None, (
                    "El generador de video necesita una foto de entrada:\n"
                    "envía una foto con el prompt, o responde a una foto con el texto."
                ), None
            if cm in ("krea2", "krea2_raw", "krea2_moody") and cl.startswith("krea_edit"):
                return None, (
                    "La edición de identidad necesita una foto de entrada:\n"
                    "envía la foto de la persona + el prompt de edición (o responde a una foto)."
                ), None
            cmd = f"MODEL='{cm}' LORA='{cl}' python3 /workspace/gen_comfy.py"
            remotes = await _comfyui_run_remote(cmd, prompt)
        else:
            name = await _comfyui_upload(image_data)
            if not name:
                return None, "No pude subir la imagen al box de ComfyUI.", None
            cmd = (
                f"MODEL='{cm}' LORA='{cl}' INPUT_IMAGE='{name}' "
                f"python3 /workspace/gen_comfy.py"
            )
            remotes = await _comfyui_run_remote(cmd, prompt)
        if not remotes:
            return None, (
                "ComfyUI no devolvió imagen. Revisa el box: "
                f"ssh -p {port} root@{COMFYUI_HOST} 'supervisorctl status comfyui'"
            ), None
        locals_ = []
        for rp in remotes:
            local = await _comfyui_pull(rp)
            if local:
                locals_.append(local)
        if not locals_:
            return None, "No pude descargar la imagen del box.", None
        return locals_, None, {"comfyui_remotes": remotes}
    except Exception as e:
        return None, f"Error de ComfyUI: {e}", None
```

#### 8. `_generate_once` (L3152-3154) — propagar meta comfyui

```python
    if prov == "comfyui":
        locals_, err, meta = await _generate_comfyui(model, prompt, image_data)
        return locals_, err, meta
```

#### 9. `_generate_comfyui_refine` + validador (insertar DESPUÉS de `_generate_comfyui`, tras L3329)

```python
def _validate_refine_remote_path(p: str) -> bool:
    """Los paths vienen del stdout del box; validar charset para poder embeberlos
    en el shell con seguridad (sin comillas simples ni meta-char)."""
    return bool(p) and len(p) <= 300 and _REFINE_REMOTE_PATH_RE.fullmatch(p) is not None


async def _generate_comfyui_refine(
    model: dict,
    prompt: str,
    remote_paths: list[str],
    *,
    status_msg: types.Message | None = None,
) -> tuple[list[str] | None, str | None]:
    """Refina bases YA generadas en el box (REFINE_ONLY=1). Timeout escalado:
    el box usa 1200s POR base (_run_graph), N bases → 1200*N + margen.
    Returns (lista de local_paths refinados, err)."""
    try:
        _ssh_base, port, err = _comfyui_ssh_base()
        if err:
            return None, err
        if not remote_paths:
            return None, "No hay imágenes base para refinar."
        invalid = [p for p in remote_paths if not _validate_refine_remote_path(p)]
        if invalid:
            return None, "Paths de refino inválidos."
        cm = model.get("comfyui_model", "krea2")
        cl = model.get("comfyui_lora", "none")
        refine_timeout = REFINE_REFINE_TIMEOUT_PER_BASE * len(remote_paths) + 300
        cmd = (
            f"MODEL='{cm}' LORA='{cl}' REFINE_ONLY='1' "
            f"REFINE_INPUT='{','.join(remote_paths)}' python3 /workspace/gen_comfy.py"
        )
        remotes = await _comfyui_run_remote(cmd, prompt, timeout=refine_timeout)
        if not remotes:
            return None, (
                "El refino no devolvió imagen. Revisa el box: "
                f"ssh -p {port} root@{COMFYUI_HOST} 'supervisorctl status comfyui'"
            )
        locals_ = []
        for rp in remotes:
            local = await _comfyui_pull(rp)
            if local:
                locals_.append(local)
        if not locals_:
            return None, "No pude descargar la imagen refinada del box."
        return locals_, None
    except Exception as e:
        return None, f"Error de ComfyUI: {e}"
```

#### 10. `_send_comfyui_image` (L3332-3368) — reply_markup/save_ref configurables + devuelve mensaje

```python
async def _send_comfyui_image(
    output: object,
    prompt: str,
    status_msg: types.Message,
    message: types.Message,
    prefix: str,
    regen_context: dict,
    model: dict | None = None,
    delete_status: bool = True,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
    save_ref: bool = True,
) -> types.Message | None:
    """Send a ComfyUI local-file result to Telegram directly (bypasses the
    URL-download machinery in process_image_result, which chokes on local paths).
    Item 2: reply_markup y save_ref configurables; devuelve el mensaje enviado
    (None si no se pudo leer el archivo)."""
    try:
        with open(str(output), "rb") as f:
            photo = BufferedInputFile(f.read(), filename="comfyui.png")
    except (OSError, TypeError):
        await status_msg.edit_text("No se pudo leer la imagen generada.")
        return None
    kb = _image_regenerate_keyboard() if reply_markup is None else reply_markup
    sent_msg = await message.answer_photo(
        photo,
        caption=_format_result_caption(prefix, prompt, model=model),
        parse_mode="HTML",
        reply_markup=kb,
        reply_to_message_id=message.message_id,
        allow_sending_without_reply=True,
    )
    if save_ref:
        sessions.save_generation_ref(
            message.chat.id,
            sent_msg.message_id,
            provider="comfyui",
            kind="image",
            prompt=prompt,
            regen=regen_context,
        )
    if delete_status:
        await status_msg.delete()
    return sent_msg
```

#### 11. `_send_comfyui_album` (L3444-3489) — save_ref + devuelve mensajes + media builder

Reemplazo de `_send_comfyui_album` y extracción del builder:

```python
def _build_comfyui_album_media(
    outputs: list, prefix: str, prompt: str, model: dict | None,
) -> list[types.InputMediaPhoto]:
    media: list = []
    for i, p in enumerate(outputs[:10]):
        try:
            with open(str(p), "rb") as f:
                data = f.read()
        except (OSError, TypeError):
            continue
        cap = _format_result_caption(prefix, prompt, model=model) if i == 0 else None
        media.append(
            types.InputMediaPhoto(
                media=BufferedInputFile(data, filename=f"comfyui_{i}.png"),
                caption=cap,
                parse_mode="HTML",
            )
        )
    return media


async def _send_comfyui_album(
    outputs: list,
    prompt: str,
    status_msg: types.Message,
    message: types.Message,
    prefix: str,
    regen_context: dict,
    *,
    model: dict | None = None,
    delete_status: bool = True,
    save_ref: bool = True,
) -> list[types.Message] | None:
    """Envía varias imágenes como álbum de Telegram (máx 10). Devuelve los mensajes
    (None si no se pudo leer ninguna)."""
    media = _build_comfyui_album_media(outputs, prefix, prompt, model)
    if not media:
        await status_msg.edit_text("No se pudieron leer las imágenes generadas.")
        return None
    sent = await message.answer_media_group(
        media,
        reply_to_message_id=message.message_id,
        allow_sending_without_reply=True,
    )
    if save_ref:
        sessions.save_generation_ref(
            message.chat.id,
            sent[0].message_id,
            provider="comfyui",
            kind="image",
            prompt=prompt,
            regen=regen_context,
        )
    if delete_status:
        await status_msg.delete()
    return sent
```

#### 12. `_send_comfyui_output` (L3412-3441) — params `meta`/`cancel_event` + choke

```python
async def _send_comfyui_output(
    model: dict,
    output: object,
    prompt: str,
    status_msg: types.Message,
    message: types.Message,
    prefix: str,
    regen_context: dict,
    *,
    delete_status: bool = True,
    meta: dict | None = None,
    cancel_event: asyncio.Event | None = None,
) -> bool:
    """Dispatch: video (MiniMax/Wan) o imagen (resto de modelos ComfyUI).
    output puede ser una LISTA (batch multi-ángulo = 5 imágenes → álbum).
    Item 2: si la generación es refinable (comfyui_refine==1 y meta trae
    comfyui_remotes) entra al flujo en 2 etapas con confirmación interactiva."""
    if _comfyui_is_video(model):
        return await _send_comfyui_video(
            output, prompt, status_msg, message, prefix, regen_context, model=model,
            delete_status=delete_status,
        )
    if (
        model.get("comfyui_refine") == "1"
        and meta is not None
        and bool(meta.get("comfyui_remotes"))
    ):
        return await _send_comfyui_confirm_refine(
            model, output, prompt, status_msg, message, prefix, regen_context, meta,
            delete_status=delete_status, cancel_event=cancel_event,
        )
    if isinstance(output, list):
        if len(output) == 1:
            output = output[0]
        elif len(output) > 1:
            return bool(
                await _send_comfyui_album(
                    output, prompt, status_msg, message, prefix, regen_context, model=model,
                    delete_status=delete_status,
                )
            )
    return (await _send_comfyui_image(
        output, prompt, status_msg, message, prefix, regen_context, model=model,
        delete_status=delete_status,
    )) is not None
```

#### 13. `_send_comfyui_confirm_refine` (insertar DESPUÉS de `_send_comfyui_album`)

```python
async def _send_comfyui_confirm_refine(
    model: dict,
    output: object,
    prompt: str,
    status_msg: types.Message,
    message: types.Message,
    prefix: str,
    regen_context: dict,
    meta: dict,
    *,
    delete_status: bool = True,
    cancel_event: asyncio.Event | None = None,
) -> bool:
    """Flujo en 2 etapas: envía la base + teclado de confirmación, espera la decisión
    (TTL = REFINE_CONFIRM_TIMEOUT) y refina o deja la base como final.
    Álbum: la base es un álbum (sin inline keyboard) → el teclado de confirmación va en
    un mensaje de texto SEPARADO; el álbum base se conserva. (A7/A8)"""
    is_album = isinstance(output, list) and len(output) > 1
    token = uuid.uuid4().hex[:8]
    job_id = getattr(cancel_event, "job_id", None)
    future = _register_pending_refine(
        token, user_id=message.from_user.id,
        message_id=message.message_id, job_id=job_id,
    )
    kb = _refine_confirm_keyboard(token)

    base_msg = None
    confirm_msg = None
    if is_album:
        await _send_comfyui_album(
            output, prompt, status_msg, message, prefix, regen_context,
            model=model, delete_status=False, save_ref=True,
        )
        confirm_msg = await message.answer(
            "¿Refinar las imágenes generadas?",
            reply_markup=kb,
        )
    else:
        single = output[0] if isinstance(output, list) else output
        base_msg = await _send_comfyui_image(
            single, prompt, status_msg, message, prefix, regen_context,
            model=model, delete_status=False, save_ref=True, reply_markup=kb,
        )
        if base_msg is None:
            _drop_pending_refine(token)
            return False

    try:
        decision = await asyncio.wait_for(future, timeout=REFINE_CONFIRM_TIMEOUT)
    except asyncio.TimeoutError:
        decision = False
    finally:
        _drop_pending_refine(token)

    if _job_cancelled(cancel_event):
        decision = _REFINE_CANCELLED

    if decision is True:
        refined, rerr = await _generate_comfyui_refine(
            model, prompt, list(meta.get("comfyui_remotes", [])),
            status_msg=status_msg,
        )
        if rerr:
            await status_msg.edit_text(rerr, reply_markup=None)
            if is_album:
                if confirm_msg is not None:
                    try:
                        await confirm_msg.delete()
                    except Exception:
                        pass
            else:
                try:
                    await base_msg.edit_reply_markup(reply_markup=_image_regenerate_keyboard())
                except Exception:
                    pass
            return True
        if is_album:
            await _send_comfyui_album(
                refined, prompt, status_msg, message, prefix, regen_context,
                model=model, delete_status=delete_status,
            )
            if confirm_msg is not None:
                try:
                    await confirm_msg.delete()
                except Exception:
                    pass
        else:
            ok = await _send_comfyui_image(
                refined, prompt, status_msg, message, prefix, regen_context,
                model=model, delete_status=delete_status,
            )
            if ok:
                try:
                    await base_msg.delete()
                except Exception:
                    pass
        return True

    if decision is _REFINE_CANCELLED:
        if is_album:
            if confirm_msg is not None:
                try:
                    await confirm_msg.delete()
                except Exception:
                    pass
        else:
            try:
                await base_msg.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
        return True

    # no / timeout → la base es final
    if is_album:
        if confirm_msg is not None:
            try:
                await safe_edit_text(confirm_msg, "Imagen final.")
            except Exception:
                pass
    else:
        try:
            await base_msg.edit_reply_markup(reply_markup=_image_regenerate_keyboard())
        except Exception:
            pass
    if delete_status and status_msg is not None:
        try:
            await status_msg.delete()
        except Exception:
            pass
    return True
```

#### 14. `_process_single_photo_edit` (L1637-1652) — pasar meta + cancel_event

```python
        if model.get("provider") == "comfyui":
            return await _send_comfyui_output(
                model,
                output,
                prompt,
                status_msg,
                message,
                "Edit",
                _build_image_regen_context(
                    model=model,
                    user_id=uid,
                    prompt=prompt,
                    mode="edit",
                    source_file_id=file_id,
                    integrate_mode=integrate_mode,
                ),
                meta=kie_meta,
                cancel_event=cancel_event,
            )
```

## Context

- `@bot.py:170` `GENERATE_MAX_RETRIES = 5` — insertar constantes/registry tras esta línea
- `@bot.py:440-443` `_cancel_job_keyboard` — pattern de builder de teclado
- `@bot.py:446-480` `_start_job`/`_job_cancelled`/`_request_cancel_job` — pattern registry + `event.job_id`
- `@bot.py:483-494` `_finish_job` — añadir cleanup de `_pending_refine`
- `@bot.py:667-673` `_image_regenerate_keyboard` — insertar helpers de registry/keyboard tras este
- `@bot.py:695-703` `_model_from_regen` — reconstruye `comfyui_refine` (consistente; no tocar)
- `@bot.py:803-848` `get_model` — modelo comfyui con `comfyui_refine` (L842); no tocar
- `@bot.py:851-873` `safe_edit_text` — para editar sin crash / sin quitar teclado por error
- `@bot.py:1098-1115` `handle_cancel_job` — añadir `_cancel_pending_refines_for_user`
- `@bot.py:1118-1188` `handle_confirm_generation` — pattern callback; insertar `handle_refine_decision` tras L1188
- `@bot.py:1255-1294` `_do_regenerate` (call site L1271) — NO tocar (ítem 3)
- `@bot.py:1455-1491` `_do_generate_text` (call site L1476) — NO tocar (ítem 3)
- `@bot.py:1637-1652` `_process_single_photo_edit` — wire `meta=kie_meta, cancel_event=cancel_event`
- `@bot.py:1690+` `_process_album_edit_from_file_ids` — NO tocar (gap pre-existente, ítem 3)
- `@bot.py:2060-2095` variables (call site L2086) — NO tocar (ítem 3)
- `@bot.py:2344-2361` reply (call site L2346) — NO tocar (ítem 3)
- `@bot.py:3130-3156` `_generate_once` — rama comfyui → devolver meta
- `@bot.py:3199-3216` `_comfyui_run_remote` — capturar `subprocess.TimeoutExpired`
- `@bot.py:3274-3329` `_generate_comfyui` — base-only + 3-tupla con meta
- `@bot.py:3332-3368` `_send_comfyui_image` — reply_markup/save_ref + devuelve mensaje
- `@bot.py:3371-3373` `_comfyui_is_video` — gate del choke (no tocar)
- `@bot.py:3412-3441` `_send_comfyui_output` — params `meta`/`cancel_event` + choke
- `@bot.py:3444-3489` `_send_comfyui_album` — save_ref + media builder extraído
- `@tests/conftest.py:45-54` `reset_runtime_state` — añadir `bot._pending_refine.clear()`
- `@/home/ubuntu/comfyui-vast-setup/gen_comfy.py:186-214` box REFINE_ONLY (ítem 1, NO desplegar; solo referencia de contrato: exit 0/2/3, stdout solo paths)

## Tasks

### Task 1: Harness `tests/test_comfyui_refine.py` en ROJO

**type:** auto
**Objective:** Los tests codifican los contratos del ítem 2 y fallan sobre el código actual
(funciones/params inexistentes). `bot.py` NO se edita en esta task.
**Files:** `/home/ubuntu/repos/grok/tests/test_comfyui_refine.py` (create), `/home/ubuntu/repos/grok/tests/conftest.py` (edit, 1 línea)
**Action:**

STRICT TDD. Capturar el baseline de la suite ANTES de escribir nada:

```bash
cd /home/ubuntu/repos/grok && ./venv/bin/python -m pytest tests -q 2>&1 | tail -5
```

Luego añadir `bot._pending_refine.clear()` al fixture `reset_runtime_state` de
`tests/conftest.py` (tras `bot._active_jobs.clear()`), y crear el harness con helpers:

- `_status_message()` — `MagicMock` con `edit_text`/`delete` = `AsyncMock`.
- `_comfyui_model(**over)` — `{"provider": "comfyui", "comfyui_model": "krea2", "comfyui_lora": "none", "comfyui_refine": "1", **over}`.
- Los tests async van `async def test_...` (pytest.ini `asyncio_mode = auto`). Cada test que toca
  `_pending_refine` empieza con `bot._pending_refine.clear()`.

Tests (cada uno debe ser ROJO sobre el código actual — ver columna RED):

| Test | Setup | Assert | RED por |
|------|-------|--------|---------|
| `test_generate_comfyui_base_only_no_refine_env` | patch `_comfyui_ssh_base`→`("ssh -p 22 root@box", 22, None)`; `_comfyui_run_remote` AsyncMock→`["/workspace/ComfyUI/output/base_a.png"]`; `_comfyui_pull` AsyncMock→`str(tmp_path/"out.png")`; `locals_, err, meta = await bot._generate_comfyui(_comfyui_model(), "a cat")` | `err is None`; `locals_ == [str(...)]`; `meta == {"comfyui_remotes": ["/workspace/ComfyUI/output/base_a.png"]}`; cmd de run_remote NO contiene `REFINE=` y sí `MODEL='krea2'` | unpack 3-tupla → ValueError + cmd tiene `REFINE='1'` |
| `test_generate_comfyui_refine_runs_refine_only` | patch ssh/pull/run (run AsyncMock→`["/workspace/ComfyUI/output/refined_base_a.png"]`); `await bot._generate_comfyui_refine(_comfyui_model(), "make it prettier", ["/workspace/ComfyUI/output/base_a.png"])` | `err is None`; `locals_ == [str(refined)]`; cmd contiene `REFINE_ONLY='1'` y `REFINE_INPUT='/workspace/ComfyUI/output/base_a.png'` y NO `INPUT_IMAGE=`; `kwargs["timeout"] >= 1200` | función inexistente → AttributeError |
| `test_generate_comfyui_refine_rejects_invalid_paths` | run AsyncMock; `await _generate_comfyui_refine(_comfyui_model(), "p", ["/workspace/ok.png", "/workspace/bad;x.png"])` | `err is not None`; `locals_ is None`; `run_remote.assert_not_called()` | idem |
| `test_refine_remote_path_validation` | llamadas puras a `bot._validate_refine_remote_path` | `"/workspace/ComfyUI/output/refined_a.png"`→True; `"/workspace/a b.png"`, `"/workspace/a;rm -rf x.png"`, `"/workspace/a\`id\`.png"`, `"/workspace/a$(id).png"`, `"/tmp/outside.png"`, `"'"`, `"/workspace/a\nb.png"`→False | fn inexistente → AttributeError |
| `test_send_comfyui_output_confirm_yes_single` | `message` (from_user.id=uid, message_id, chat.id), `status_msg`, `event = asyncio.Event()` con `event.job_id="job1"`; patch `_send_comfyui_image` AsyncMock `side_effect=[base_msg, refined_msg]` (MagicMocks truthy con `delete`/`edit_reply_markup` AsyncMock); patch `_generate_comfyui_refine` AsyncMock→`(["/tmp/refined.png"], None)`; lanzar `task = asyncio.create_task(bot._send_comfyui_output(_comfyui_model(), "/tmp/base.png", "prompt", status_msg, message, "Edit", regen_ctx, meta={"comfyui_remotes": ["/workspace/ComfyUI/output/base_a.png"]}, cancel_event=event))`; loop `for _ in range(100): if bot._pending_refine: break; await asyncio.sleep(0)`; tomar `token = next(iter(bot._pending_refine))`; callback `MagicMock(from_user.id=uid, data=f"refine:{token}:yes", answer=AsyncMock(), message=MagicMock())`; `await bot.handle_refine_decision(cb)`; `result = await asyncio.wait_for(task, timeout=5)` | `result is True`; `_generate_comfyui_refine` llamado con remotes `["/workspace/ComfyUI/output/base_a.png"]`; `_send_comfyui_image` llamado 2× (base y refinado), el refinado con `output="/tmp/refined.png"` y `delete_status=True`; `base_msg.delete.assert_awaited_once()` | `_send_comfyui_output` no acepta `meta=`/`cancel_event=` → TypeError + `handle_refine_decision` inexistente |
| `test_send_comfyui_output_confirm_no_keeps_base` | igual que yes pero `cb.data = f"refine:{token}:no"` | `result is True`; `_generate_comfyui_refine` NO llamado; `_send_comfyui_image` llamado 1× (base); `base_msg.edit_reply_markup.assert_awaited_once()` y su `reply_markup` es `_image_regenerate_keyboard()`; `status_msg.delete.assert_awaited_once()` | idem |
| `test_send_comfyui_output_confirm_timeout_finalizes` | igual que no pero `monkeypatch.setattr(bot, "REFINE_CONFIRM_TIMEOUT", 0)` (wait_for expira al instante) | `_generate_comfyui_refine` NO llamado; `base_msg.edit_reply_markup` con regen kb; `result is True` | idem |
| `test_send_comfyui_output_confirm_yes_album` | `output = ["/tmp/a.png", "/tmp/b.png"]` (álbum); patch `_send_comfyui_album` AsyncMock `side_effect=[base_msgs, refined_msgs]`; `message.answer` AsyncMock→`confirm_msg` (MagicMock con `delete`/`edit_text` AsyncMock); patch `_generate_comfyui_refine`→`(["/tmp/r1.png", "/tmp/r2.png"], None)`; resolver `yes` | `_send_comfyui_album` llamado 2×; el 2º (refinado) con `output=["/tmp/r1.png", "/tmp/r2.png"]` y `delete_status=True`; `confirm_msg.delete.assert_awaited_once()`; el álbum base NO se borra (sin `delete()` sobre base_msgs) | idem |
| `test_refine_decision_stale_and_retap_idempotent` | `bot._pending_refine = {}`; `cb.data="refine:deadbeef:yes"`, `cb.answer=AsyncMock()` → 1ª llamada "ya se procesó" con `show_alert=True`. Luego registrar una entry con future; resolver `yes` una vez; volver a llamar el handler con el mismo cb → `future.done()` true → no-op (sin InvalidStateError) | 1ª: `cb.answer` con show_alert=True; 2ª: future sigue `True`, sin crash | handler inexistente → AttributeError |
| `test_refine_decision_wrong_user_ignored` | registrar entry (user 1, future); `cb.from_user.id = 2` | `cb.answer` con "No es tu confirmación"; future NO resuelto (`future.done() is False`) | idem |
| `test_handle_cancel_job_resolves_pending_refine` | `event = bot._start_job(1, "edit")`; registrar entry (user 1, `job_id=event.job_id`); `cb.data = f"cancel_job:{event.job_id}"`, `cb.from_user.id = 1`, `cb.answer=AsyncMock()`, `cb.message=MagicMock(text="", caption=None, edit_text=AsyncMock())`; `await bot.handle_cancel_job(cb)` | `future.result() is bot._REFINE_CANCELLED` | sentinel/fn inexistentes → AttributeError |
| `test_finish_job_cleans_pending_refine` | `event = bot._start_job(1, "edit")`; registrar entry (user 1, `job_id=event.job_id`); `bot._finish_job(1, event)` | `token not in bot._pending_refine`; `future.result() is False` | registry inexistente → AttributeError |

Correr y confirmar que TODOS fallan (RED) sobre el código actual:

```bash
cd /home/ubuntu/repos/grok && ./venv/bin/python -m pytest tests/test_comfyui_refine.py -q
```

**Verification:**

```bash
cd /home/ubuntu/repos/grok && ./venv/bin/python -m pytest tests/test_comfyui_refine.py -q
```

**Done:** Los 12 tests existen. Todos fallan/erran sobre el código actual (RED por los motivos
de la columna "RED por"). `bot.py` NO fue editado. Baseline de la suite capturado (comando del Action).

### Task 2: Implementar la maquinaria en `bot.py`

**type:** auto
**Objective:** `bot.py` cumple los contratos del harness; suite del harness verde.
**Files:** `/home/ubuntu/repos/grok/bot.py` (14 ediciones, ver "Exact implementation")
**Action:**

Aplicar EXACTAMENTE los 14 bloques de "Architecture Approach § Exact implementation", en orden:
constantes/registry (1), keyboard+helpers (2), handler `handle_refine_decision` (3),
`handle_cancel_job` (4), `_finish_job` (5), `_comfyui_run_remote` (6), `_generate_comfyui` (7),
`_generate_once` (8), `_generate_comfyui_refine`+validador (9), `_send_comfyui_image` (10),
`_send_comfyui_album`+builder (11), `_send_comfyui_output` (12), `_send_comfyui_confirm_refine` (13),
`_process_single_photo_edit` (14).

NO:
- Tocar los 4 call sites no-wired (1271/1476/2086/2346).
- Tocar `_process_album_edit_from_file_ids`, `_model_from_regen`, `get_model`, `_comfyui_is_video`,
  `process_image_result`, `_generate_kie_once`, ni el flujo de retry de `generate_image`.
- Cambiar valores por defecto de refino ni el cmd de video.
- Añadir dependencias (solo stdlib + aiogram ya importados).

Después, re-correr el harness hasta verde:

```bash
cd /home/ubuntu/repos/grok && ./venv/bin/python -m pytest tests/test_comfyui_refine.py -q
```

Syntax check:

```bash
cd /home/ubuntu/repos/grok && ./venv/bin/python -c "import ast; ast.parse(open('bot.py').read()); print('ok')"
```

**Verification:**

```bash
cd /home/ubuntu/repos/grok && ./venv/bin/python -m pytest tests/test_comfyui_refine.py -q
cd /home/ubuntu/repos/grok && ./venv/bin/python -c "import ast; ast.parse(open('bot.py').read()); print('ok')"
```

**Done:** Los 12 tests pasan. Sintaxis OK. Los no-touch listados arriba quedan intactos (verificar
con `git diff --stat`).

### Task 3: Regresión + commit work-unit

**type:** auto
**Objective:** La suite completa no introduce regresiones vs el baseline de Task 1; commit único.
**Files:** `/home/ubuntu/repos/grok/bot.py`, `/home/ubuntu/repos/grok/tests/test_comfyui_refine.py`, `/home/ubuntu/repos/grok/tests/conftest.py`
**Action:**

Correr la suite completa y comparar con el baseline de Task 1 (no deben aparecer fallos NUEVOS):

```bash
cd /home/ubuntu/repos/grok && ./venv/bin/python -m pytest tests -q 2>&1 | tail -10
```

Si aparece un fallo nuevo en un archivo que este ítem NO tocó (variables/album/kie/cancel), parar
e investigar antes de commitear — no commitear con regresión nueva.

Commit único (work-unit) con los 3 archivos:

```bash
cd /home/ubuntu/repos/grok
git add bot.py tests/test_comfyui_refine.py tests/conftest.py
git commit -m "feat(comfyui): two-stage refine with interactive confirmation"
```

Sin `Co-Authored-By`. Verificar con `git status --short` y `git log --oneline -1`.

**Verification:**

```bash
cd /home/ubuntu/repos/grok && ./venv/bin/python -m pytest tests/test_comfyui_refine.py -q
cd /home/ubuntu/repos/grok && git status --short
cd /home/ubuntu/repos/grok && git log --oneline -1
```

**Done:** Harness verde. Suite completa sin fallos nuevos vs baseline. Commit único convencional,
sin `Co-Authored-By`; `git status` limpio (solo el commit nuevo).

## Instrucciones para gsd-executor

- **TDD order es obligatorio:** Task 1 (tests RED) → Task 2 (impl) → Task 3 (regresión + commit).
  No implementar primero.
- **Repo y runner:** cambio en `/home/ubuntu/repos/grok` (monolito aiogram). Runner:
  `cd /home/ubuntu/repos/grok && ./venv/bin/python -m pytest` (pytest 8.4.2, `asyncio_mode = auto`;
  NO hace falta `@pytest.mark.asyncio`). Capturar baseline de `pytest tests -q` al inicio de Task 1.
- **Skills a cargar ANTES de trabajar** (leer el SKILL.md completo de cada una):
  - `telegram-bot-hardener` — el ítem toca handlers aiogram + registry runtime + teclados;
    respetar sus reglas de handlers acotados y tests (pytest + pytest-asyncio/auto).
  - `work-unit-commits` — commit único work-unit, conventional, sin `Co-Authored-By`.
  - `test-quality-flow` — el harness debe validar comportamiento (los 12 tests de Task 1),
    no documentar código roto.
- **Patterns to copy (mecánico, no rediseñar):** `_image_regenerate_keyboard` L667-673;
  `_start_job`/`_request_cancel_job` L446-480 (registry + `event.job_id`); `handle_confirm_generation`
  L1118-1188 (callback + filtro startswith); `safe_edit_text` L851-873; try/except silencioso de
  `handle_cancel_job` L1106-1113 para edit/delete de mensajes.
- **Gotchas (no pisar):**
  - Editar un mensaje sin `reply_markup` ELIMINA el teclado inline → en el flujo usar
    `edit_reply_markup(...)` explícito o `safe_edit_text` con `reply_markup` explícito.
  - El teclado `regen` exige `callback.message.photo` (L1192) → NUNCA ponerlo en un mensaje de
    texto (caso álbum usa "Imagen final." sin teclado — A7).
  - `_send_comfyui_image`/`_send_comfyui_album` cambian su retorno (Message/list | None): su ÚNICO
    caller es `_send_comfyui_output` — actualizar SOLO ahí (bloque 12).
  - El refino NO pasa por `GENERATE_MAX_RETRIES`; su error handling es propio (bloque 9, try/except).
  - `_comfyui_run_remote` debe capturar `subprocess.TimeoutExpired` (bloque 6) — sin eso el refino
    crashea en vez de devolver error limpio.
  - NO mockear `os.path.exists` en el harness; usar archivos temp reales (`tmp_path`) para
    `_comfyui_pull` (ya es AsyncMock, devolver `str(tmp_path/...)`).
  - `aioresponses` NO aplica (comfyui usa subprocess SSH). No usar HTTP mocks.
- **No-touch verificable con `git diff --stat`:** los 4 call sites no-wired, `_model_from_regen`,
  `get_model`, `_comfyui_is_video`, `_process_album_edit_from_file_ids`, `process_image_result`,
  `_generate_kie_once`, el retry de `generate_image`, y todo lo de `comfyui-vast-setup` (box).
- **Commit:** mensaje `feat(comfyui): two-stage refine with interactive confirmation`, SIN
  `Co-Authored-By`. Un solo commit con los 3 archivos.
- Si descubres gotchas no obvios, guárdalos en engram vía `mem_save` con `project: 'grok'` y
  `topic_key: 'architecture/comfyui-refine-confirm'`.

## Test commands

Harness (primario):

```bash
cd /home/ubuntu/repos/grok && ./venv/bin/python -m pytest tests/test_comfyui_refine.py -q
```

Regresión completa:

```bash
cd /home/ubuntu/repos/grok && ./venv/bin/python -m pytest tests -q
```

Syntax check:

```bash
cd /home/ubuntu/repos/grok && ./venv/bin/python -c "import ast; ast.parse(open('bot.py').read()); print('ok')"
```

## Risks + Mitigation

| Risk (from impact) | Mitigation | Where |
|--------------------|------------|-------|
| MEDIUM — Timeout SSH 600s (bot.py:3210) vs `_run_graph(timeout=1200)` del box: N bases → 1200*N | Timeout escalado `1200*N+300` en `_generate_comfyui_refine` + `subprocess.TimeoutExpired` capturado en `_comfyui_run_remote` (devuelve `[]`, no lanza) | bloques 6 y 9; `test_generate_comfyui_refine_runs_refine_only` |
| MEDIUM — Refino lento deja el slot de job ocupado si el usuario no decide | TTL `REFINE_CONFIRM_TIMEOUT` (default 300s, env-configurable) → base final si no decide | bloque 13 (`asyncio.wait_for`); `test_..._confirm_timeout_finalizes` |
| MEDIUM — Cancelar durante el await deja el future colgado / base con teclado huérfano | `handle_cancel_job` fuerza-resuelve a `_REFINE_CANCELLED`; el flujo re-chequea `_job_cancelled` y limpia; `_finish_job` limpia huérfanos | bloques 4, 5, 13; `test_handle_cancel_job_resolves_pending_refine` |
| MEDIUM — `_send_comfyui_image`/`_send_comfyui_album` cambian de retorno (bool → Message/list) | ÚNICO caller es `_send_comfyui_output` (verificado); se actualiza solo ahí; los helpers de URL (L3973/4002) NO se tocan | bloques 10-12; regresión completa Task 3 |
| MEDIUM — En "no/timeout" la base queda sin `generation_ref` → botón Regenerar roto | `save_ref=True` en la base (single y álbum) → regen funciona (A8) | bloque 13 |
| LOW — Shell quoting de `REFINE_INPUT` | Regex `^/workspace/[A-Za-z0-9_./-]{1,300}$` + fail-closed (path inválido → error sin correr) | bloque 9; `test_refine_remote_path_validation` |
| MEDIUM — Regresión: base-only cambia los otros 4 call sites (mandan base sin refinar) | Estado intermedio ACEPTADO (ítem 3 wirea); no-touch explícito; sin meta el choke no se activa (A3/A4) | Scope/Non-goals; `git diff --stat` Task 3 |
| MEDIUM — Handler `refine:` re-tap / token stale puede doble-resolver el future (InvalidStateError) | Guard `future.done()` + `entry is None` → no-op idempotente | bloque 3; `test_refine_decision_stale_and_retap_idempotent` |
| LOW — Teclado regen sobre mensaje de texto (álbum) rompe (`handle_regenerate_image` exige photo) | Álbum: "no/timeout" → msg de confirmación → "Imagen final." SIN regen kb (A7) | bloque 13; `test_..._confirm_yes_album` |
| LOW — Test registry `_pending_refine` fuga entre tests | `conftest.py` limpia `_pending_refine` en `reset_runtime_state` (1 línea) | conftest; Task 1 |

## Success Criteria

- [ ] `_generate_comfyui` no pasa `REFINE=` al box (base-only) y devuelve `meta["comfyui_remotes"]`.
- [ ] `_generate_once` comfyui y `generate_image` propagan ese meta.
- [ ] `_generate_comfyui_refine` corre `REFINE_ONLY='1' REFINE_INPUT='<CSV validado>'` con timeout
      `1200*N+300`; paths inválidos → error sin correr el remoto.
- [ ] Single refine="1": base con teclado confirm; yes → refinado (regen kb + gen_ref) y base borrada;
      no/timeout → base final con teclado `_image_regenerate_keyboard()`.
- [ ] Álbum: base álbum conservada; teclado de confirmación en mensaje de texto separado; yes → álbum
      refinado nuevo y msg de confirmación borrado; no/timeout → msg → "Imagen final.".
- [ ] `handle_refine_decision` valida token+user; re-tap stale/resuelto = no-op idempotente.
- [ ] `handle_cancel_job` fuerza-resuelve pendientes a `_REFINE_CANCELLED`; el flujo re-chequea
      `_job_cancelled` tras el await; `_finish_job` limpia huérfanos.
- [ ] `_comfyui_run_remote` captura `subprocess.TimeoutExpired`.
- [ ] Video (wan_i2v) y call sites sin meta: comportamiento actual intacto (sin `REFINE=` en base-only).
- [ ] Harness `tests/test_comfyui_refine.py`: 12 tests — RED en Task 1, GREEN en Task 2 y 3.
- [ ] Suite completa sin fallos NUEVOS vs baseline; commit único `feat(comfyui): two-stage refine with
      interactive confirmation` sin `Co-Authored-By`; `git status` limpio.
- [ ] No-touch intacto: 4 call sites no-wired, `_process_album_edit_from_file_ids`, variables,
      `comfyui-vast-setup` (sin deploy).
