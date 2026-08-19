from __future__ import annotations

import asyncio
import base64
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.parse
import uuid
from io import BytesIO
from pathlib import Path

from typing import Any, Awaitable, Callable

import aiohttp
import httpx
import replicate
from aiogram import BaseMiddleware, Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    TelegramObject,
)
from dotenv import load_dotenv

import sessions
import variables_store
import download

load_dotenv()

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
REPLICATE_TOKEN = os.environ["REPLICATE_API_TOKEN"]
XAI_API_KEY = os.environ["XAI_API_KEY"]
KIE_API_KEY = os.environ.get("KIE_API_KEY", "")
os.environ["REPLICATE_API_TOKEN"] = REPLICATE_TOKEN

# ComfyUI provider — SSH target (Vast box). Host/port change per instance.
COMFYUI_HOST = os.environ.get("COMFYUI_HOST", "")
COMFYUI_PORT = os.environ.get("COMFYUI_PORT", "22")


def _parse_allowed_telegram_ids() -> set[int] | None:
    raw = os.environ.get("ALLOWED_TELEGRAM_IDS", "").strip()
    if not raw:
        return None
    return {int(item.strip()) for item in raw.split(",") if item.strip()}


def _parse_admin_telegram_ids() -> set[int] | None:
    """Optional explicit admin list for the /listas panel.

    When unset, the panel is available to every allowed user (ALLOWED_TELEGRAM_IDS),
    and to everyone when the allowlist is also unset.
    """
    raw = os.environ.get("VARIABLES_ADMIN_IDS", "").strip()
    if not raw:
        return None
    return {int(item.strip()) for item in raw.split(",") if item.strip()}


ALLOWED_TELEGRAM_IDS = _parse_allowed_telegram_ids()
VARIABLES_ADMIN_IDS = _parse_admin_telegram_ids()
TELEGRAM_MAX_CAPTION_LEN = 1024
TELEGRAM_CAPTION_COLLECT_THRESHOLD = 1020
TELEGRAM_MAX_TEXT_LEN = 4096
# /variables N — max images per batch
VARIABLES_MAX = 10

SOURCES_DIR = Path(__file__).parent / "sources"
INTEGRATE_REFS_DIR = Path(__file__).parent / "integrate_refs"
INTEGRATE_MAX_ALBUM = 10

# --- Model Registry ---
MODELS = {
    "grok": {
        "key": "grok",
        # Base identifiers (variant-specific id/replicate_id resolved at runtime via get_grok_imagine_config + get_model)
        "id": "grok-imagine-image-quality",           # default/fallback (xAI direct)
        "replicate_id": "xai/grok-imagine-image-quality",
        "name": "Grok Imagine",
        "desc": "xAI Grok Imagine",
        "provider": "xai",
    },
    "seedream": {
        "key": "seedream",
        "id": "bytedance/seedream-5-lite",
        "name": "Seedream 5.0",
        "desc": "ByteDance Seedream 5.0 Lite",
        "provider": "replicate",
    },
    "faceswap": {
        "key": "faceswap",
        "id": "cdingram/face-swap:d1d6ea8c8be89d664a07a457526f7128109dee7030fdac424788d762c71ed111",
        "name": "Face Swap",
        "desc": "Intercambio de caras (cdingram/face-swap)",
        "provider": "replicate",
    },
    "grok_video": {
        "key": "grok_video",
        "id": "grok-imagine-video",
        "name": "Grok Imagine Video",
        "desc": "Generación de video con xAI Grok Imagine",
        "provider": "xai",
    },
    "comfyui": {
        "key": "comfyui",
        "id": "comfyui",
        "name": "ComfyUI (GPU propia)",
        "desc": "Generación/edición con ComfyUI en la GPU de Vast: Krea 2 / Moody (imagen) y Wan 2.2 (video)",
        "provider": "comfyui",
    },
}

VIDEO_MODEL_LABELS = {
    "grok-imagine-video": "Base",
    "grok-imagine-video-1.5": "1.5 (reciente)",
}
VIDEO_MODE_LABELS = {
    "fun": "Fun",
    "normal": "Normal",
    "spicy": "Spicy",
}

DEFAULT_MODEL = "grok"

# Granular Grok Imagine configuration (independent, persistent flow).
# Three providers (xAI direct / Replicate / Kie.ai) × two quality tiers.
# Research-backed identifiers (xAI API + Replicate xai/ mirrors):
#   - standard: fast, grok-imagine-image / xai/grok-imagine-image
#   - quality : higher fidelity, better text/detail/2K, grok-imagine-image-quality / xai/grok-imagine-image-quality
GROK_IMAGINE_VARIANTS = {
    "standard": {
        "id": "grok-imagine-image",
        "replicate_id": "xai/grok-imagine-image",
        "kie_id": "grok-imagine-image-2-0/text-to-image",
        "label": "Estándar",
        "desc": "Rápido, ideal para prototipado y previews",
    },
    "quality": {
        "id": "grok-imagine-image-quality",
        "replicate_id": "xai/grok-imagine-image-quality",
        "kie_id": "grok-imagine-image-2-0/text-to-image",
        "label": "Alta calidad",
        "desc": "Mayor detalle, texto nítido, hasta 2K (recomendado para finales)",
    },
}
DEFAULT_GROK_IMAGINE_PROVIDER = "kie"
DEFAULT_GROK_IMAGINE_VARIANT = "quality"

# xAI video generation polling
VIDEO_POLL_INTERVAL_SEC = 5
VIDEO_MAX_POLL_SEC = 600  # 10 minutes
IMAGE_MAX_POLL_SEC = 120  # 2 minutes — image tasks rarely need more
I2V_MAX_IMAGE_BYTES = 5 * 1024 * 1024
DOWNLOAD_TIMEOUT_SEC = 120
DOWNLOAD_MAX_BYTES = 50 * 1024 * 1024
TELEGRAM_MAX_VIDEO_BYTES = 50 * 1024 * 1024
POLL_MAX_RETRIES = 3
POLL_RETRY_BACKOFF_SEC = (2, 4, 8)
GENERATE_MAX_RETRIES = 5
# --- ComfyUI refine: 2-stage generation (base + interactive confirm) ---
REFINE_CONFIRM_TIMEOUT = int(os.environ.get("REFINE_CONFIRM_TIMEOUT", "300"))  # segundos
REFINE_REFINE_TIMEOUT_PER_BASE = 1200  # el box usa _run_graph(timeout=1200) POR base (gen_comfy.py:173)
_REFINE_CANCELLED = object()  # sentinel: future resuelto por cancelación
_pending_refine: dict[str, dict] = {}  # token -> {future, user_id, message_id, job_id}
_REFINE_REMOTE_PATH_RE = re.compile(r"^/workspace/[A-Za-z0-9_./-]{1,300}$")
# xAI serves generated assets from *.x.ai / *.xai.com only (no broad CDN suffixes).
ALLOWED_DOWNLOAD_HOST_SUFFIXES = (".x.ai", ".xai.com")
# Kie.ai result and upload CDN hosts (exact + subdomain suffixes from API probes).
KIE_DOWNLOAD_HOSTS = frozenset({
    "kieai.redpandaai.co",
    "static.aiquickdraw.com",
    "tempfile.redpandaai.co",
    "tempfile.aiquickdraw.com",
    "file.aiquickdraw.com",
})
KIE_DOWNLOAD_HOST_SUFFIXES = (".aiquickdraw.com", ".redpandaai.co")
KIE_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT_SEC)

# Replicate face swap — API allows Prefer: wait=x only between 1 and 60 seconds.
# Jobs that take longer are completed via the SDK's prediction.wait() polling loop.
REPLICATE_WAIT_SEC = 60
REPLICATE_RATE_LIMIT_SEC = 10
REPLICATE_HTTP_TIMEOUT = httpx.Timeout(
    connect=10.0,
    read=90.0,
    write=120.0,
    pool=10.0,
)
TELEGRAM_MEDIA_GROUP_MAX = 10
FACESWAP_PROGRESS_WIDTH = 10

# (GROK_PROVIDERS removed — replaced by the granular GROK_IMAGINE_VARIANTS + unified /config FSM)

# Per-user in-memory cache (hydrated from sessions.py persistence on first access).
# Keys: model (top-level), grok_imagine_provider + grok_imagine_variant (granular Imagine config),
#       source_path, fs_state, pending_prompt, pending_faceswap_file_ids,
#       awaiting_long_prompt_text, pending_edit_file_ids,
#       pending_edit_integrate_mode, pending_edit_is_video.
# Model, provider, and model-specific settings are configured via the unified /config FSM
# (/config, /model, /imagine, /imaginess, /video).
user_state: dict[int, dict] = {}


def _escape_prompt(prompt: str) -> str:
    return html.escape(prompt)


def _video_status_message(model_id: str, detail: str, prompt: str) -> str:
    """Video generation status line with model id in bold (HTML parse_mode)."""
    return (
        f"Generando video con <b>{html.escape(model_id)}</b>... {detail}\n\n"
        f"<i>{_escape_prompt(prompt)}</i>"
    )


def _video_start_message(model_id: str, prompt: str) -> str:
    """Initial video generation message before polling updates (HTML parse_mode)."""
    return (
        f"Generando video con <b>{html.escape(model_id)}</b>...\n\n"
        f"<i>{_escape_prompt(prompt)}</i>"
    )


def _xai_user_error(context: str = "generación") -> str:
    return f"Error en la {context}. Intenta de nuevo más tarde."


def _kie_user_error(context: str = "generación") -> str:
    return f"Error en la {context}. Intenta de nuevo más tarde."


def _retry_backoff(attempt: int) -> int:
    return POLL_RETRY_BACKOFF_SEC[min(attempt, len(POLL_RETRY_BACKOFF_SEC) - 1)]


def _prov_label(prov: str) -> str:
    return {"xai": "xAI", "replicate": "Replicate", "kie": "Kie.ai"}.get(prov, prov)


_KIE_NOT_CONFIGURED_MSG = "Kie.ai no está disponible en este momento. Contacta al administrador del bot."
_XAI_NOT_CONFIGURED_MSG = (
    "xAI no está disponible en este momento. Contacta al administrador del bot."
)
_REPLICATE_NOT_CONFIGURED_MSG = (
    "Replicate no está disponible en este momento. Contacta al administrador del bot."
)
_KIE_PRIVACY_NOTICE = (
    "Los prompts e imágenes se envían a servidores de Kie.ai (tercero) para procesamiento."
)
_KIE_QUALITY_NOTE = (
    "Nota Kie.ai: Alta calidad aplica solo a imágenes. En video solo está disponible el modo estándar."
)
_SENSITIVE_DOWNLOAD_WARNING = (
    "\n\n⚠️ Enlace temporal con tu contenido generado; no lo compartas públicamente."
)


def _log_xai_error(status: int, request_id: str | None = None) -> None:
    suffix = f" request_id={request_id}" if request_id else ""
    print(f"[xAI error] status={status}{suffix}")


def _xai_http_ok(status: int) -> bool:
    """xAI video endpoints may return 200 or 202 (Accepted) while async work is in flight."""
    return status in (200, 202)


def _is_user_allowed(user_id: int) -> bool:
    if ALLOWED_TELEGRAM_IDS is None:
        return True
    return user_id in ALLOWED_TELEGRAM_IDS


def _is_bot_command_message(message: types.Message) -> bool:
    """True when the message is a Telegram bot command (e.g. /config), not user prompt text."""
    if not message.text:
        return False
    if message.entities:
        for ent in message.entities:
            if ent.type == "bot_command" and ent.offset == 0:
                return True
    # Fallback for mocks/tests without entities
    stripped = message.text.lstrip()
    return stripped.startswith("/") and len(stripped) > 1 and stripped[1].isalnum()


def _is_generation_prompt_message(message: types.Message) -> bool:
    """Plain user text for image/video generation — excludes commands and replies."""
    return bool(
        message.text
        and not message.reply_to_message
        and not _is_bot_command_message(message)
    )


def _validate_prompt(prompt: str, *, max_len: int = TELEGRAM_MAX_TEXT_LEN) -> str | None:
    if len(prompt) < 3:
        return "El prompt es muy corto. Dame algo mas descriptivo."
    if len(prompt) > max_len:
        return f"El prompt es demasiado largo (máximo {max_len} caracteres)."
    return None


COMFYUI_CAPTION_MODEL_LABELS = {
    "qwen": "Qwen-Image-Edit 2511",
    "krea2": "Krea 2",
    "krea2_raw": "Krea 2 RAW",
    "krea2_moody": "Moody (Krea 2 Mix)",
    "wan_i2v": "Wan 2.2",
}
COMFYUI_CAPTION_LORA_LABELS = {
    "none": "Sin LoRA",
    "lightning": "Lightning 4 pasos",
    "multiangle": "Multi-ángulo (auto)",
    "multiangle_batch": "Multi-ángulo ×5 (auto)",
    "krea_nsfw": "NSFW V4",
    "krea_snapshot": "Realistic Snapshot",
    "krea_both": "NSFW V4 + Realistic Snapshot",
    "krea_snofs": "SNOFS v1.3D",
    "qwen_snofs": "SNOFS v1.3",
    "krea_edit": "✏️ Editar (Identity)",
    "krea_edit_nsfw": "✏️ Editar + NSFW",
    "krea_edit_snapshot": "✏️ Editar + Snapshot",
    "krea_edit_both": "✏️ Editar + NSFW + Snapshot",
    "lightx2v": "lightx2v (rápido)",
}


def _truncate_prompt_short(prompt: str, max_lines: int = 3, max_chars: int = 150) -> str:
    """Primeras 2-3 líneas del prompt (máx ~150 chars), con … si se corta."""
    lines = [ln.strip() for ln in prompt.splitlines() if ln.strip()]
    if not lines:
        return "…"
    parts, used = [], 0
    for ln in lines:
        if used >= max_chars or len(parts) >= max_lines:
            break
        piece = ln[: max_chars - used]
        parts.append(piece)
        used += len(piece)
    text = " ".join(parts).strip()
    if len(text) < len(" ".join(lines)):
        text = text.rstrip() + "…"
    return _escape_prompt(text)


def _format_model_caption(model: dict, prompt: str) -> str:
    """Caption con Modelo / LoRA (ComfyUI) / Prompt truncado a 2-3 líneas."""
    cm = model.get("comfyui_model")
    if cm:
        mlabel = COMFYUI_CAPTION_MODEL_LABELS.get(cm, cm) or "?"
        cl = model.get("comfyui_lora")
        llabel = COMFYUI_CAPTION_LORA_LABELS.get(cl, cl) or "Sin LoRA"
        body = (
            f"<b>Modelo:</b> {_escape_prompt(mlabel)}\n"
            f"<b>LoRA:</b> {_escape_prompt(llabel)}\n"
            f"<b>Prompt:</b> "
        )
    else:
        mlabel = model.get("name", "?")
        body = f"<b>Modelo:</b> {_escape_prompt(mlabel)}\n<b>Prompt:</b> "
    return body + _truncate_prompt_short(prompt)


def _format_result_caption(
    prefix: str,
    prompt: str,
    variant: str | None = None,
    model: dict | None = None,
) -> str:
    if model is not None:
        return _format_model_caption(model, prompt)
    header = f"<b>{prefix} ({variant}):</b> " if variant else f"<b>{prefix}:</b> "
    ellipsis = "…"
    max_len = TELEGRAM_MAX_CAPTION_LEN
    if len(header) >= max_len:
        return header[:max_len]

    budget = max_len - len(header)
    escaped_full = _escape_prompt(prompt)
    if len(escaped_full) <= budget:
        return f"{header}{escaped_full}"

    if budget <= len(ellipsis):
        return f"{header}{ellipsis[:budget]}"

    content_budget = budget - len(ellipsis)
    lo, hi = 0, len(prompt)
    best = 0
    while lo <= hi:
        mid = (lo + hi) // 2
        if len(_escape_prompt(prompt[:mid])) <= content_budget:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1

    truncated = _escape_prompt(prompt[:best]) + ellipsis
    return f"{header}{truncated}"


def _parse_integrate_caption(caption: str) -> tuple[bool, str]:
    """Return (integrate_mode, prompt) stripping leading /s trigger from caption."""
    text = caption.strip()
    if not text.startswith("/s"):
        return False, text
    prompt = re.sub(r"^/s(?:\s+|$)", "", text, count=1).strip()
    return True, prompt


def _prompt_needs_long_text_collection(prompt: str) -> bool:
    return len(prompt) > TELEGRAM_CAPTION_COLLECT_THRESHOLD


def _confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Confirmar", callback_data="confirm:yes"),
         InlineKeyboardButton(text="Cancelar", callback_data="confirm:no")],
    ])


# In-flight cancellable jobs (per user). Cooperative cancel: checked between
# batch items / after long awaits. Up to MAX_ACTIVE_JOBS_PER_USER at once.
MAX_ACTIVE_JOBS_PER_USER = 3
_JOBS_FULL_MSG = (
    "Ya hay 3 procesos en curso. Espera a que termine uno o cancélalo."
)
_active_jobs: dict[int, list[dict]] = {}


def _cancel_job_keyboard(event: asyncio.Event | None = None) -> InlineKeyboardMarkup:
    job_id = getattr(event, "job_id", None) if event is not None else None
    data = f"cancel_job:{job_id}" if job_id else "cancel_job"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Cancelar", callback_data=data)],
        ]
    )


def _start_job(user_id: int, kind: str) -> asyncio.Event | None:
    """Register a cancellable job. Does not cancel other jobs for this user.

    Returns None when the user already has MAX_ACTIVE_JOBS_PER_USER running.
    """
    jobs = _active_jobs.setdefault(user_id, [])
    if len(jobs) >= MAX_ACTIVE_JOBS_PER_USER:
        return None
    event = asyncio.Event()
    job_id = uuid.uuid4().hex[:8]
    event.job_id = job_id
    jobs.append({"id": job_id, "event": event, "kind": kind})
    return event


def _job_cancelled(event: asyncio.Event | None) -> bool:
    return bool(event is not None and event.is_set())


def _request_cancel_job(user_id: int, job_id: str | None = None) -> bool:
    jobs = _active_jobs.get(user_id) or []
    if not jobs:
        return False
    if job_id:
        for job in jobs:
            if job["id"] == job_id:
                job["event"].set()
                return True
        return False
    for job in reversed(jobs):
        if not job["event"].is_set():
            job["event"].set()
            return True
    jobs[-1]["event"].set()
    return True


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
    # Force-close pending refine confirmations owned by this job — the user can
    # no longer answer them once the job is gone.
    job_id = getattr(event, "job_id", None)
    for token in list(_pending_refine):
        entry = _pending_refine[token]
        if entry["user_id"] == user_id and (job_id is None or entry["job_id"] == job_id):
            if not entry["future"].done():
                entry["future"].set_result(False)
            _pending_refine.pop(token, None)


def _clear_pending_faceswap(state: dict) -> None:
    state["pending_faceswap_file_ids"] = None


def _set_long_prompt_collection(
    state: dict,
    *,
    file_ids: list[str],
    integrate_mode: bool,
    is_video: bool,
) -> None:
    state["pending_prompt"] = None
    _clear_pending_faceswap(state)
    state["awaiting_long_prompt_text"] = True
    state["pending_edit_file_ids"] = file_ids
    state["pending_edit_integrate_mode"] = integrate_mode
    state["pending_edit_is_video"] = is_video


def _clear_long_prompt_collection(state: dict) -> None:
    state["awaiting_long_prompt_text"] = False
    state["pending_edit_file_ids"] = None
    state["pending_edit_integrate_mode"] = False
    state["pending_edit_is_video"] = False


def _is_awaiting_long_prompt_text(state: dict) -> bool:
    return bool(state.get("awaiting_long_prompt_text"))


async def _long_prompt_collection_reply(
    message: types.Message,
    *,
    is_video: bool,
    n_photos: int,
) -> None:
    if is_video:
        action = "animar la imagen"
    elif n_photos > 1:
        action = "editar las imágenes"
    else:
        action = "editar la imagen"
    album_note = ""
    if n_photos > 1:
        album_note = f"\n\nHe guardado tus {n_photos} fotos del álbum."
    await message.answer(
        "El caption es demasiado largo para procesarlo directamente.\n\n"
        f"Envíame el prompt como <b>mensaje de texto</b> para {action}.{album_note}",
        parse_mode="HTML",
    )


def _integrate_ref_path(user_id: int) -> Path | None:
    path = sessions.get_session(user_id).get("integrate_ref_path")
    if not path:
        return None
    ref_path = Path(path)
    return ref_path if ref_path.exists() else None


def _load_integrate_ref_bytes(user_id: int) -> tuple[BytesIO | None, str | None]:
    ref_path = _integrate_ref_path(user_id)
    if ref_path is None:
        return None, (
            "No hay imagen de referencia configurada. "
            "Usa /cambiar_referencia para establecerla."
        )
    return BytesIO(ref_path.read_bytes()), None


def _validate_integrate_prerequisites(model: dict, user_id: int) -> tuple[BytesIO | None, str | None]:
    if model.get("provider") != "xai":
        return None, (
            "La edición con referencia (/s) requiere el proveedor "
            "<b>xAI (oficial)</b>. Cambialo en /config."
        )
    return _load_integrate_ref_bytes(user_id)


def _detect_image_mime(image_data: BytesIO) -> tuple[str, str]:
    """Return (mime_type, file_extension) from image magic bytes."""
    image_data.seek(0)
    header = image_data.read(16)
    image_data.seek(0)
    if header[:3] == b"\xff\xd8\xff":
        return "image/jpeg", "jpg"
    if header[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png", "png"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "image/webp", "webp"
    return "image/jpeg", "jpg"


def _image_to_data_uri(image_data: BytesIO, mime: str | None = None) -> str:
    if mime is None:
        mime, _ = _detect_image_mime(image_data)
    image_data.seek(0)
    b64 = base64.b64encode(image_data.read()).decode()
    return f"data:{mime};base64,{b64}"


def _validate_image_for_i2v(image_data: BytesIO) -> str | None:
    image_data.seek(0, os.SEEK_END)
    size = image_data.tell()
    image_data.seek(0)
    if size > I2V_MAX_IMAGE_BYTES:
        max_mb = I2V_MAX_IMAGE_BYTES // 1024 // 1024
        got_mb = max(1, size // 1024 // 1024)
        return f"La imagen es demasiado grande ({got_mb} MB). Máximo {max_mb} MB."
    return None


def _is_allowed_download_host(host: str) -> bool:
    host = (host or "").lower()
    if host in ("x.ai",):
        return True
    return any(host.endswith(suffix) for suffix in ALLOWED_DOWNLOAD_HOST_SUFFIXES)


def _is_allowed_kie_download_host(host: str) -> bool:
    host = (host or "").lower()
    if host in KIE_DOWNLOAD_HOSTS:
        return True
    return any(host.endswith(suffix) for suffix in KIE_DOWNLOAD_HOST_SUFFIXES)


def _is_allowed_kie_asset_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        return False
    return _is_allowed_kie_download_host(parsed.hostname or "")


def _download_allowlist_for_provider(provider: str | None) -> str | None:
    """Return download allowlist key for a provider, or None for no host check."""
    if provider == "xai":
        return "xai"
    if provider == "kie":
        return "kie"
    return None


def _is_host_allowed_for_download(host: str, allowlist: str | None) -> bool:
    if allowlist == "xai":
        return _is_allowed_download_host(host)
    if allowlist == "kie":
        return _is_allowed_kie_download_host(host)
    return True


def get_video_provider_for_user(user_id: int) -> str:
    """Effective video backend. Replicate has no video API — falls back to xAI."""
    prov = get_grok_imagine_config(user_id)["provider"]
    if prov == "replicate":
        return "xai"
    return prov


async def _download_telegram_photo(photo: types.PhotoSize) -> BytesIO:
    return await _download_telegram_file_id(photo.file_id)


async def _download_telegram_file_id(file_id: str) -> BytesIO:
    file = await bot.get_file(file_id)
    file_bytes = await bot.download_file(file.file_path)
    file_bytes.seek(0)
    image_data = BytesIO(file_bytes.read())
    image_data.name = "image.jpg"
    return image_data


def _image_regenerate_keyboard(*, show_cancel: bool = False) -> InlineKeyboardMarkup:
    """Keyboard under generated images. show_cancel=True while a job is running
    (e.g. regeneration in progress on a status message)."""
    row = [InlineKeyboardButton(text="Regenerar", callback_data="regen")]
    if show_cancel:
        row.append(InlineKeyboardButton(text="Cancelar", callback_data="cancel_job"))
    return InlineKeyboardMarkup(inline_keyboard=[row])


def _refine_confirm_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✨ Refinar", callback_data=f"refine:{token}:yes"),
                InlineKeyboardButton(text="⏭ Continuar", callback_data=f"refine:{token}:no"),
            ],
        ]
    )


def _refining_keyboard() -> InlineKeyboardMarkup:
    """Placeholder for the base while refining. The confirm choice can't be
    re-tapped during the (potentially long) refine step — the callback is a
    no-op handled by handle_refine_noop."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Refinando…", callback_data="refine_noop")],
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


def _cancel_pending_refines_for_user(user_id: int, job_id: str | None = None) -> None:
    """Force-resolve pending refine confirmations to cancelled.

    With job_id, only the matching job's confirmations are resolved — cancelling
    one of several concurrent jobs must not convert another job's base to final.
    job_id=None resolves every pending confirmation for the user (mirrors
    _request_cancel_job's no-id fallback)."""
    for token in list(_pending_refine):
        entry = _pending_refine[token]
        if entry["user_id"] != user_id:
            continue
        if job_id is not None and entry["job_id"] != job_id:
            continue
        if not entry["future"].done():
            entry["future"].set_result(_REFINE_CANCELLED)


def _grok_model_for_config(user_id: int, provider: str, variant: str) -> dict:
    m = dict(MODELS["grok"])
    spec = GROK_IMAGINE_VARIANTS.get(variant, GROK_IMAGINE_VARIANTS[sessions.DEFAULT_GROK_IMAGINE_VARIANT])
    if provider == "replicate":
        model_id = spec["replicate_id"]
    elif provider == "kie":
        model_id = spec["kie_id"]
    else:
        model_id = spec["id"]
    prov_label = _prov_label(provider)
    m["provider"] = provider
    m["id"] = model_id
    m["name"] = f"Grok Imagine ({prov_label} • {spec['label']})"
    m["desc"] = f"xAI Grok Imagine — {prov_label} • {spec['label']}: {spec['desc']}"
    m["imagine_provider"] = provider
    m["imagine_variant"] = variant
    return m


def _model_from_regen(regen: dict) -> dict:
    key = regen.get("model_key", DEFAULT_MODEL)
    if key == "grok":
        prov = regen.get("imagine_provider", sessions.DEFAULT_GROK_IMAGINE_PROVIDER)
        var = regen.get("imagine_variant", sessions.DEFAULT_GROK_IMAGINE_VARIANT)
        return _grok_model_for_config(regen["user_id"], prov, var)
    if key == "comfyui":
        return get_model(regen["user_id"])
    return MODELS.get(key, MODELS[DEFAULT_MODEL])


def _build_image_regen_context(
    *,
    model: dict,
    user_id: int,
    prompt: str,
    mode: str,
    source_file_id: str | None = None,
    kie_source_ref: dict | None = None,
    integrate_mode: bool = False,
) -> dict:
    ctx: dict = {
        "mode": mode,
        "model_key": model["key"],
        "user_id": user_id,
        "prompt": prompt,
        "provider": model.get("provider", "?"),
    }
    if model["key"] == "grok":
        cfg = get_grok_imagine_config(user_id)
        ctx["imagine_provider"] = model.get("imagine_provider", cfg["provider"])
        ctx["imagine_variant"] = model.get("imagine_variant", cfg["variant"])
    if source_file_id:
        ctx["source_file_id"] = source_file_id
    if kie_source_ref:
        ctx["kie_source_ref"] = {
            "task_id": kie_source_ref["task_id"],
            "index": kie_source_ref.get("index", 0),
        }
    if integrate_mode:
        ctx["integrate_mode"] = True
    return ctx


def _resolve_reply_kie_ref(reply_to_message: types.Message | None) -> dict | None:
    """Return Kie task_id ref when replying to a bot-generated Kie image."""
    if reply_to_message is None or not reply_to_message.photo:
        return None
    ref = sessions.get_generation_ref(reply_to_message.chat.id, reply_to_message.message_id)
    if not ref or ref.get("provider") != "kie" or not ref.get("kie_task_id"):
        return None
    return {
        "task_id": ref["kie_task_id"],
        "index": ref.get("kie_index", 0),
    }


def get_user_state(user_id: int) -> dict:
    if user_id not in user_state:
        # Hydrate from disk persistence (sessions.py now stores model + imagine granular config)
        persisted = sessions.get_session(user_id)
        user_state[user_id] = {
            "model": persisted.get("model", sessions.DEFAULT_MODEL),
            "grok_imagine_provider": persisted.get("grok_imagine_provider", sessions.DEFAULT_GROK_IMAGINE_PROVIDER),
            "grok_imagine_variant": persisted.get("grok_imagine_variant", sessions.DEFAULT_GROK_IMAGINE_VARIANT),
            "source_path": persisted.get("source_path"),
            "integrate_ref_path": persisted.get("integrate_ref_path"),
            "fs_state": persisted.get("state", sessions.FsState.IDLE),
            "integrate_ref_awaiting": False,
            "pending_prompt": None,
            "pending_faceswap_file_ids": None,
            "awaiting_long_prompt_text": False,
            "pending_edit_file_ids": None,
            "pending_edit_integrate_mode": False,
            "pending_edit_is_video": False,
        }
    return user_state[user_id]


def get_grok_imagine_config(user_id: int) -> dict:
    """Return the current granular (provider + variant) config for Grok Imagine.
    This is the source of truth for the independent persistent Imagine settings.
    Falls back to module defaults (which match the ones in sessions).
    """
    state = get_user_state(user_id)
    prov = state.get("grok_imagine_provider", sessions.DEFAULT_GROK_IMAGINE_PROVIDER)
    var = state.get("grok_imagine_variant", sessions.DEFAULT_GROK_IMAGINE_VARIANT)
    if prov not in ("xai", "replicate", "kie"):
        prov = sessions.DEFAULT_GROK_IMAGINE_PROVIDER
    if var not in GROK_IMAGINE_VARIANTS:
        var = sessions.DEFAULT_GROK_IMAGINE_VARIANT
    spec = GROK_IMAGINE_VARIANTS[var]
    if prov == "replicate":
        model_id = spec["replicate_id"]
    elif prov == "kie":
        model_id = spec["kie_id"]
    else:
        model_id = spec["id"]
    return {
        "provider": prov,
        "variant": var,
        "id": model_id,
        "label": spec["label"],
        "desc": spec["desc"],
        "prov_label": _prov_label(prov),
    }


def get_model(user_id: int) -> dict:
    """Return a concrete model dict for generation (and for display in UI).
    For the 'grok' (Grok Imagine) key the dict is dynamically built from the
    independent granular config (provider + standard/quality variant).
    Non-grok models are returned as-is from the static registry.
    """
    key = get_user_state(user_id)["model"]
    base = MODELS.get(key, MODELS[DEFAULT_MODEL])
    if key == "grok":
        m = dict(base)
        cfg = get_grok_imagine_config(user_id)
        m["provider"] = cfg["provider"]
        m["id"] = cfg["id"]  # already resolved to short (xAI) or full (Replicate)
        m["name"] = f"Grok Imagine ({cfg['prov_label']} • {cfg['label']})"
        m["desc"] = f"xAI Grok Imagine — {cfg['prov_label']} • {cfg['label']}: {cfg['desc']}"
        # keep a couple of extra fields for convenience in status messages
        m["imagine_provider"] = cfg["provider"]
        m["imagine_variant"] = cfg["variant"]
        return m
    if key == "grok_video":
        m = dict(base)
        cfg = get_grok_imagine_config(user_id)
        video_prov = get_video_provider_for_user(user_id)
        m["provider"] = video_prov
        prov_label = _prov_label(video_prov)
        if cfg["provider"] == "replicate":
            m["name"] = f"Grok Imagine Video ({prov_label}; imágenes: Replicate)"
            m["desc"] = "Generación de video con xAI; imágenes vía Replicate"
        else:
            m["name"] = f"Grok Imagine Video ({prov_label})"
            m["desc"] = f"Generación de video con Grok Imagine — {prov_label}"
        m["imagine_provider"] = cfg["provider"]
        m["imagine_variant"] = cfg["variant"]
        return m
    if key == "comfyui":
        m = dict(base)
        cc = sessions.get_comfyui_config(user_id)
        m["comfyui_model"] = cc["model"]
        m["comfyui_lora"] = cc["lora"]
        m["comfyui_refine"] = cc["refine"]
        m["name"] = f"ComfyUI ({cc['model']} • lora {cc['lora']})"
        m["desc"] = (
            f"ComfyUI en la GPU — modelo {cc['model']}, LoRA {cc['lora']}"
        )
        return m
    return base


async def safe_edit_text(
    message: types.Message,
    text: str,
    **kwargs,
) -> bool:
    """Edit message text (and optional reply_markup etc.) safely.

    Ignores the benign 'message is not modified' error that Telegram returns
    when you attempt to edit a message to the exact same content + markup
    (very common when user re-taps the currently selected option in a keyboard
    that shows checkmarks).

    Returns:
        True if the edit went through, False if it was a no-op (same content).
    Raises any other TelegramBadRequest or unexpected errors.
    """
    try:
        await message.edit_text(text, **kwargs)
        return True
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return False
        raise


bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


class AllowlistMiddleware(BaseMiddleware):
    """Block all message/callback handlers when ALLOWED_TELEGRAM_IDS is configured."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        from_user = getattr(event, "from_user", None)
        user_id = getattr(from_user, "id", None) if from_user is not None else None
        if ALLOWED_TELEGRAM_IDS is not None and (user_id is None or not _is_user_allowed(user_id)):
            answer = getattr(event, "answer", None)
            if answer is not None:
                if isinstance(event, types.CallbackQuery):
                    await event.answer("No tienes permiso para usar este bot.", show_alert=True)
                else:
                    await event.answer("No tienes permiso para usar este bot.")
            return None
        return await handler(event, data)


dp.message.middleware(AllowlistMiddleware())
dp.callback_query.middleware(AllowlistMiddleware())


# ---------------------------------------------------------------------------
# /listas admin panel (variables lists)
#
# Registered here (before the generic text handlers) so the FSM text-input
# handlers win over handle_text/handle_reply_edit while the admin is typing
# an item/template; StateFilter keeps them inert for every other message.
# ---------------------------------------------------------------------------
import variables_flow

_VARS_DEPS = {
    "safe_edit_text": safe_edit_text,
    "allowed_telegram_ids": ALLOWED_TELEGRAM_IDS,
    "variables_admin_ids": VARIABLES_ADMIN_IDS,
}

variables_flow.register_variables_handlers(dp, _VARS_DEPS)

# Re-exports for tests (variables admin panel)
cmd_listas = variables_flow.cmd_listas
handle_var_open = variables_flow.handle_var_open
handle_var_add = variables_flow.handle_var_add
handle_var_edit_list = variables_flow.handle_var_edit_list
handle_var_del_list = variables_flow.handle_var_del_list
handle_var_item_edit = variables_flow.handle_var_item_edit
handle_var_item_del = variables_flow.handle_var_item_del
handle_var_tmpl = variables_flow.handle_var_tmpl
handle_var_back = variables_flow.handle_var_back
handle_var_close = variables_flow.handle_var_close
handle_var_cancel = variables_flow.handle_var_cancel
handle_add_text = variables_flow.handle_add_text
handle_edit_text = variables_flow.handle_edit_text
handle_template_text = variables_flow.handle_template_text

_VAR_INPUT_HANDLERS = {
    variables_flow._state_key(variables_flow.VarStates.add_item): handle_add_text,
    variables_flow._state_key(variables_flow.VarStates.edit_text): handle_edit_text,
    variables_flow._state_key(variables_flow.VarStates.template): handle_template_text,
}


async def _delegate_variables_input(message: types.Message) -> bool:
    """Run the active /listas panel text-input handler for this user, if any.

    Defensive guard: the FSM text handlers are registered before the generic
    handlers (handle_text / handle_reply_edit), so this normally never fires.
    It guarantees that a text sent while the admin is adding/editing a list
    item or template can never be intercepted by the image-generation
    confirmation, even if the registration order ever regresses.
    """
    try:
        from aiogram.fsm.context import FSMContext
        from aiogram.fsm.storage.base import StorageKey

        key = StorageKey(
            chat_id=message.chat.id,
            user_id=message.from_user.id,
            bot_id=bot.id,
            thread_id=getattr(message, "message_thread_id", None),
        )
        state = await dp.storage.get_state(key)
    except Exception as exc:
        # Fail open (normal generation flow) but surface the lookup problem.
        print(f"[variables] FSM state lookup failed: {exc}")
        return False
    handler = _VAR_INPUT_HANDLERS.get(state)
    if handler is None:
        return False
    ctx = FSMContext(storage=dp.storage, key=key)
    # Let handler errors propagate to the dispatcher error middleware, exactly
    # as they would on the normal FSM path.
    await handler(message, ctx)
    return True


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    state = get_user_state(message.from_user.id)
    model = get_model(message.from_user.id)

    if state["model"] == "grok_video":
        lines = [
            "Envame un prompt y te genero un <b>video</b>.\n",
            "Ejemplo: <i>un gato descansando en un rayo de sol, moviendo la cola suavemente</i>\n",
            "Tambien puedes enviar una <b>foto con caption</b> para animarla (imagen a video):\n",
            "la IA tomara tu imagen y generara un video segun el caption.\n",
        ]
    elif state["model"] == "faceswap":
        lines = [
            "Modo <b>Face Swap</b> activo.\n",
            "Usa /cambiar_source para configurar la cara fuente.\n",
            "Luego envia fotos para intercambiar las caras.\n",
            "Tambien puedes enviar albumes de fotos.\n",
        ]
        if state["source_path"]:
            lines.insert(2, "Source ya configurado. Envia tus fotos.\n")
    else:
        lines = [
            "Envame un prompt y te genero la imagen (o video si eliges Grok Imagine Video).\n",
            "Ejemplo: <i>a cat wearing a wizard hat in a neon-lit cyberpunk alley</i>\n",
            "Tambien puedes enviar una <b>foto con caption</b> para editarla o animarla:\n",
            "la IA tomara tu imagen y aplicara los cambios que describas en el caption.\n",
            "Tambien puedes enviar un <b>album de fotos con caption</b> para editarlas todas con el mismo prompt.\n",
            "Para combinar una imagen fija con cada foto del album, usa /cambiar_referencia "
            "y pon <b>/s</b> al inicio del caption (requiere proveedor xAI en /config).\n",
        ]

    lines.append(f"Modelo actual: <b>{model['name']}</b>\n")
    lines.append("Usa /config para cambiar de modelo o ajustar opciones.")

    await message.answer("".join(lines), parse_mode="HTML")


# ---------------------------------------------------------------------------
# Video config helpers (used by config_flow and generation)
# ---------------------------------------------------------------------------
KIE_BASE_VIDEO_ASPECT_RATIOS = ("16:9", "9:16", "1:1", "3:2", "2:3")
KIE_15_VIDEO_ASPECT_RATIOS = ("16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3")


def _kie_aspect_ratios_for_model(video_model: str) -> tuple[str, ...]:
    if video_model == "grok-imagine-video-1.5":
        return KIE_15_VIDEO_ASPECT_RATIOS
    return KIE_BASE_VIDEO_ASPECT_RATIOS


def _maybe_reset_kie_aspect_ratio(
    user_id: int,
    *,
    video_model: str | None = None,
) -> str | None:
    """Reset persisted aspect ratio when invalid for Kie provider/model. Returns new ratio or None."""
    if get_video_provider_for_user(user_id) != "kie":
        return None
    cfg = sessions.get_video_config(user_id)
    model = video_model or cfg["model"]
    allowed = _kie_aspect_ratios_for_model(model)
    if cfg["aspect_ratio"] in allowed:
        return None
    fallback = (
        sessions.DEFAULT_VIDEO_ASPECT_RATIO
        if sessions.DEFAULT_VIDEO_ASPECT_RATIO in allowed
        else allowed[0]
    )
    sessions.set_video_config(user_id, aspect_ratio=fallback)
    return fallback


def _kie_video_status_label(video_model: str, *, image_to_video: bool) -> str:
    """Human-readable model id for status messages, including Kie fallbacks."""
    if video_model == "grok-imagine-video-1.5" and not image_to_video:
        return f"{video_model} (Kie.ai usa modelo base para texto→video)"
    return video_model


def _sanitize_kie_fail_log(fail_msg: str | None, limit: int = 80) -> str:
    if not fail_msg:
        return ""
    text = str(fail_msg).replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _kie_map_duration(duration: int) -> int:
    """kie.ai accepts 6–30 seconds for base text-to-video."""
    return max(6, min(duration, 30))


def _video_duration_display(configured: int, provider: str) -> str:
    if provider == "kie":
        effective = _kie_map_duration(configured)
        if effective != configured:
            return f"{configured}s → {effective}s (Kie.ai)"
        return f"{configured}s"
    return f"{configured}s"


def _video_config_summary(user_id: int) -> str:
    cfg = sessions.get_video_config(user_id)
    prov = get_video_provider_for_user(user_id)
    model_label = VIDEO_MODEL_LABELS.get(cfg["model"], cfg["model"])
    dur = _video_duration_display(cfg["duration"], prov)
    summary = f"<b>{model_label}</b> • {dur} • {cfg['aspect_ratio']} • {cfg['resolution']}"
    if prov == "kie":
        mode_label = VIDEO_MODE_LABELS.get(cfg["mode"], cfg["mode"])
        summary += f" • {mode_label}"
    return summary


@dp.callback_query(lambda c: bool(c.data) and c.data.startswith("cancel_job"))
async def handle_cancel_job(callback: types.CallbackQuery):
    """Cancel an in-flight faceswap / image-edit / regenerate job."""
    data = callback.data or ""
    job_id = data.split(":", 1)[1] if data.startswith("cancel_job:") else None
    if _request_cancel_job(callback.from_user.id, job_id=job_id):
        _cancel_pending_refines_for_user(callback.from_user.id, job_id=job_id)
        await callback.answer("Cancelando…")
        # Running loop will update final status; soft-signal on the message.
        if callback.message is not None:
            try:
                current = callback.message.text or callback.message.caption or ""
                hint = "⏹ Cancelando…"
                text = f"{current}\n\n{hint}" if current and hint not in current else (current or hint)
                await safe_edit_text(callback.message, text, reply_markup=None)
            except Exception:
                pass
        return
    await callback.answer("No hay proceso en curso.", show_alert=True)


@dp.callback_query(lambda c: c.data and c.data.startswith("confirm:"))
async def handle_confirm_generation(callback: types.CallbackQuery):
    action = callback.data.split(":", 1)[1]
    state = get_user_state(callback.from_user.id)

    if action == "no":
        state["pending_prompt"] = None
        _clear_pending_faceswap(state)
        await callback.message.edit_text("Generacion cancelada.")
        await callback.answer()
        return

    file_ids = state.get("pending_faceswap_file_ids")
    if file_ids:
        _clear_pending_faceswap(state)
        state["pending_prompt"] = None
        await callback.message.edit_text("Procesando face swap...", reply_markup=None)
        await callback.answer()
        if len(file_ids) == 1:
            await _execute_faceswap_single(
                callback.message,
                file_ids[0],
                user_id=callback.from_user.id,
                status_msg=callback.message,
            )
        else:
            await _execute_faceswap_batch(
                callback.message,
                file_ids,
                user_id=callback.from_user.id,
                status_msg=callback.message,
            )
        return

    prompt = state.get("pending_prompt")
    state["pending_prompt"] = None

    if not prompt:
        await callback.message.edit_text("Ya no hay nada pendiente. Envia una imagen o prompt nuevo.")
        await callback.answer()
        return

    model = get_model(callback.from_user.id)
    safe_prompt = _escape_prompt(prompt)
    if model["key"] == "grok_video":
        video_model = sessions.get_video_config(callback.from_user.id)["model"]
        await safe_edit_text(
            callback.message,
            _video_start_message(video_model, prompt),
            parse_mode="HTML",
            reply_markup=None,
        )
        await callback.answer()
        await _do_generate_video(
            callback.message,
            model,
            prompt,
            user_id=callback.from_user.id,
            status_msg=callback.message,
            reply_message=callback.message,
        )
        return

    await callback.message.edit_text(
        f"Generando imagen con {model['name']}...\n\n<i>{safe_prompt}</i>",
        parse_mode="HTML",
        reply_markup=None,
    )
    await callback.answer()
    await _do_generate_text(callback.message, model, prompt, user_id=callback.from_user.id)


@dp.callback_query(lambda c: c.data == "refine_noop")
async def handle_refine_noop(callback: types.CallbackQuery):
    """Tapping the "Refinando…" placeholder while a refine is in flight is a
    no-op — answer so the client doesn't leave a spinner on the button."""
    await callback.answer()


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


@dp.callback_query(lambda c: c.data == "regen")
async def handle_regenerate_image(callback: types.CallbackQuery):
    if callback.message is None or not callback.message.photo:
        await callback.answer("Mensaje no valido.", show_alert=True)
        return

    ref = sessions.get_generation_ref(callback.message.chat.id, callback.message.message_id)
    regen = ref.get("regen") if ref else None
    if not regen:
        await callback.answer("No se puede regenerar (contexto expirado).", show_alert=True)
        return

    prompt = regen.get("prompt", "").strip()
    prompt_err = _validate_prompt(prompt)
    if prompt_err:
        await callback.answer(prompt_err, show_alert=True)
        return

    model = _model_from_regen(regen)
    mode = regen.get("mode", "text")
    await callback.answer("Regenerando...")

    uid = callback.from_user.id
    cancel_event = _start_job(uid, "regen")
    if cancel_event is None:
        await callback.answer(_JOBS_FULL_MSG, show_alert=True)
        return
    status_msg = await callback.message.answer(
        f"Regenerando imagen con {model['name']}...",
        reply_markup=_cancel_job_keyboard(cancel_event),
    )
    image_data = None
    reference_image = None
    kie_source_ref = regen.get("kie_source_ref")
    source_file_id = regen.get("source_file_id")
    integrate_mode = bool(regen.get("integrate_mode"))

    try:
        if mode == "edit" and not kie_source_ref:
            if integrate_mode:
                if source_file_id:
                    image_data = await _download_telegram_file_id(source_file_id)
                else:
                    await status_msg.edit_text(
                        "No se pudo recuperar la imagen original para regenerar.",
                        reply_markup=None,
                    )
                    return
                reference_image, ref_err = _load_integrate_ref_bytes(regen["user_id"])
                if ref_err:
                    await status_msg.edit_text(ref_err, reply_markup=None)
                    return
            elif source_file_id:
                image_data = await _download_telegram_file_id(source_file_id)
            else:
                await status_msg.edit_text(
                    "No se pudo recuperar la imagen original para regenerar.",
                    reply_markup=None,
                )
                return

        if _job_cancelled(cancel_event):
            await status_msg.edit_text("⏹ Regeneración cancelada.", reply_markup=None)
            return

        output, err, kie_meta = await generate_image(
            model,
            prompt,
            image_data,
            reference_image=reference_image,
            kie_source_ref=kie_source_ref,
            status_msg=status_msg,
            status_label=f"Regenerando imagen con {model['name']}...",
        )
        if _job_cancelled(cancel_event):
            await status_msg.edit_text("⏹ Regeneración cancelada.", reply_markup=None)
            return
        if err:
            await status_msg.edit_text(err, reply_markup=None)
            return

        if model.get("provider") == "comfyui":
            await _send_comfyui_output(
                model,
                output,
                prompt,
                status_msg,
                callback.message,
                "Edit" if mode == "edit" else "Prompt",
                regen,
                meta=kie_meta,
                cancel_event=cancel_event,
            )
            return

        prefix = "Edit" if mode == "edit" else "Prompt"
        await process_image_result(
            output,
            prompt,
            status_msg,
            callback.message,
            prefix,
            download_allowlist=_download_allowlist_for_provider(model.get("provider")),
            kie_meta=kie_meta,
            regen_context=regen,
            model=model,
        )
    except replicate.exceptions.ReplicateError as e:
        backend = _prov_label(model.get("provider", "?"))
        await status_msg.edit_text(f"Error de {backend}: {e}", reply_markup=None)
    except Exception as e:
        await status_msg.edit_text(f"Error inesperado: {e}", reply_markup=None)
    finally:
        _finish_job(uid, cancel_event)


# ---------------------------------------------------------------------------
# /cambiar_source  (solo faceswap)
# ---------------------------------------------------------------------------
@dp.message(Command("cambiar_source"))
async def cmd_cambiar_source(message: types.Message):
    state = get_user_state(message.from_user.id)
    if state["model"] != "faceswap":
        await message.answer(
            "Este comando solo esta disponible en modo <b>Face Swap</b>.\n"
            "Usa /config para cambiar al modo Face Swap.",
            parse_mode="HTML",
        )
        return

    state["fs_state"] = sessions.FsState.AWAITING_SOURCE
    sessions.set_state(message.from_user.id, sessions.FsState.AWAITING_SOURCE)
    await message.answer("Envia tu foto source (la cara que quieres usar para el swap).")


# ---------------------------------------------------------------------------
# /cambiar_referencia  (solo grok — imagen fija para edición /s)
# ---------------------------------------------------------------------------
@dp.message(Command("cambiar_referencia"))
async def cmd_cambiar_referencia(message: types.Message):
    state = get_user_state(message.from_user.id)
    if state["model"] != "grok":
        await message.answer(
            "Este comando solo esta disponible en modo <b>Grok Imagine</b>.\n"
            "Usa /config para cambiar al modo Grok Imagine.",
            parse_mode="HTML",
        )
        return

    state["integrate_ref_awaiting"] = True
    await message.answer(
        "Envia la foto que sera tu <b>referencia fija</b>.\n\n"
        "Luego, en un album (o foto) con caption que empiece por <b>/s</b>, "
        "cada imagen se editara junto con esta referencia.\n"
        "Requiere proveedor <b>xAI (oficial)</b> en /config.",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# /estado
# ---------------------------------------------------------------------------
@dp.message(Command("estado"))
async def cmd_estado(message: types.Message):
    state = get_user_state(message.from_user.id)
    model = get_model(message.from_user.id)

    lines = [
        "Estado\n",
        f"Modelo: {model['name']}\n",
    ]

    if state["model"] == "faceswap":
        has_source = bool(state["source_path"])
        lines.append(f"Source: {'Configurado' if has_source else 'No configurado'}\n")
        lines.append(f"Estado: {state['fs_state']}")
    else:
        if model.get("key") == "grok":
            prov = model.get("imagine_provider") or model.get("provider", "?")
            var = model.get("imagine_variant", "?")
            var_label = GROK_IMAGINE_VARIANTS.get(var, {}).get("label", var)
            prov_labels = {"xai": "xAI (oficial)", "replicate": "Replicate", "kie": "Kie.ai"}
            prov_label = prov_labels.get(prov, prov)
            lines.append(f"API / Backend: {prov_label} • {var_label}\n")
            has_ref = _integrate_ref_path(message.from_user.id) is not None
            lines.append(
                f"Referencia integrate (/s): {'Configurada' if has_ref else 'No configurada'}\n"
            )
            lines.append("Listo para generar/editar imagenes.")
        elif model.get("key") == "grok_video":
            uid = message.from_user.id
            video_cfg = sessions.get_video_config(uid)
            video_prov = get_video_provider_for_user(uid)
            prov_labels = {"xai": "xAI (oficial)", "replicate": "Replicate", "kie": "Kie.ai"}
            lines.append(f"API / Backend: {prov_labels.get(video_prov, video_prov)}\n")
            if model.get("imagine_provider") == "replicate":
                lines.append("(Imágenes: Replicate; video vía xAI)\n")
            model_label = VIDEO_MODEL_LABELS.get(video_cfg["model"], video_cfg["model"])
            dur_label = _video_duration_display(video_cfg["duration"], video_prov)
            lines.append(
                f"Video: {model_label}, {dur_label}, "
                f"{video_cfg['aspect_ratio']}, {video_cfg['resolution']}\n"
            )
            lines.append("Listo para generar videos (texto o imagen a video).")
            lines.append("Usa /config (o /video) para configurar modelo, duración, aspecto y resolución.")
        else:
            lines.append("Listo para generar/editar imagenes.")

    await message.answer("\n".join(lines))


# ---------------------------------------------------------------------------
# TEXT messages — route by model
# ---------------------------------------------------------------------------
@dp.message(_is_generation_prompt_message)
async def handle_text(message: types.Message):
    if _is_bot_command_message(message):
        return

    # Defensive: never let /listas panel text-input be intercepted by the
    # generation confirmation (the FSM handlers normally catch it first).
    if await _delegate_variables_input(message):
        return

    state = get_user_state(message.from_user.id)

    if _is_awaiting_long_prompt_text(state):
        await _complete_long_prompt_collection(message, message.text.strip())
        return

    if state["model"] == "faceswap":
        if state["source_path"]:
            await message.answer(
                "Envia una <b>foto</b> para hacer el face swap.\n"
                "Usa /cambiar_source si quieres cambiar la cara fuente.",
                parse_mode="HTML",
            )
        else:
            await message.answer(
                "Primero configura tu cara fuente con /cambiar_source.\n"
                "Luego enviame fotos para intercambiar las caras.",
            )
        return

    # --- grok / grok_video / seedream: text → generate image or video ---
    prompt = message.text.strip()
    prompt_err = _validate_prompt(prompt)
    if prompt_err:
        await message.answer(prompt_err)
        return

    model = get_model(message.from_user.id)

    if model["key"] in ("grok", "grok_video"):
        _clear_pending_faceswap(state)
        state["pending_prompt"] = prompt
        media_word = "video" if model["key"] == "grok_video" else "imagen"
        await message.answer(
            f"¿Confirmas generar este {media_word}?\n\n<i>{_escape_prompt(prompt)}</i>",
            parse_mode="HTML",
            reply_markup=_confirmation_keyboard(),
        )
        return

    await _do_generate_text(message, model, prompt)


async def _do_generate_text(
    message: types.Message,
    model: dict,
    prompt: str,
    *,
    user_id: int | None = None,
):
    uid = user_id if user_id is not None else message.from_user.id
    status_msg = await message.answer(f"Generando imagen con {model['name']}...")

    backend = _prov_label(model.get("provider", "?"))
    try:
        output, err, kie_meta = await generate_image(
            model,
            prompt,
            status_msg=status_msg,
            status_label=f"Generando imagen con {model['name']}...",
        )
        if err:
            await status_msg.edit_text(err)
            return
        if model.get("provider") == "comfyui":
            await _send_comfyui_output(
                model,
                output,
                prompt,
                status_msg,
                message,
                "Prompt",
                _build_image_regen_context(
                    model=model,
                    user_id=uid,
                    prompt=prompt,
                    mode="text",
                ),
                meta=kie_meta,
                cancel_event=None,
            )
            return
        await process_image_result(
            output,
            prompt,
            status_msg,
            message,
            "Prompt",
            download_allowlist=_download_allowlist_for_provider(model.get("provider")),
            kie_meta=kie_meta,
            regen_context=_build_image_regen_context(
                model=model,
                user_id=uid,
                prompt=prompt,
                mode="text",
            ),
            model=model,
        )
    except replicate.exceptions.ReplicateError as e:
        await status_msg.edit_text(f"Error de {backend}: {e}")
    except Exception as e:
        await status_msg.edit_text(f"Error inesperado: {e}")


async def _do_generate_video(
    message: types.Message,
    model: dict,
    prompt: str,
    image_data: BytesIO | None = None,
    *,
    user_id: int | None = None,
    status_msg: types.Message | None = None,
    reply_message: types.Message | None = None,
    kie_source_ref: dict | None = None,
) -> bool:
    uid = user_id if user_id is not None else message.from_user.id
    reply_msg = reply_message or message

    try:
        if image_data and kie_source_ref is None:
            size_err = _validate_image_for_i2v(image_data)
            if size_err:
                if status_msg:
                    await status_msg.edit_text(size_err)
                else:
                    await reply_msg.answer(size_err)
                return False

        if status_msg is None:
            video_model = sessions.get_video_config(uid)["model"]
            if image_data or kie_source_ref:
                status_text = (
                    f"Animando imagen con <b>{html.escape(video_model)}</b>...\n\n"
                    f"<i>{_escape_prompt(prompt)}</i>"
                )
            else:
                status_text = _video_start_message(video_model, prompt)
            status_msg = await reply_msg.answer(status_text, parse_mode="HTML")

        output, err = await generate_video(
            model,
            prompt,
            image_data,
            kie_source_ref=kie_source_ref,
            status_msg=status_msg,
            user_id=uid,
        )
        if err:
            await status_msg.edit_text(err)
            return False
        prefix = "Edit" if image_data or kie_source_ref else "Prompt"
        await process_video_result(
            output,
            prompt,
            status_msg,
            reply_msg,
            prefix,
            download_allowlist=_download_allowlist_for_provider(model.get("provider")),
        )
        return True
    except Exception as e:
        print(f"[video] unexpected error user={uid}: {e}")
        if status_msg:
            await status_msg.edit_text("Error inesperado. Intenta de nuevo.")
        else:
            await reply_msg.answer("Error inesperado. Intenta de nuevo.")
        return False


async def _process_single_photo_edit(
    message: types.Message,
    prompt: str,
    file_id: str,
    *,
    integrate_mode: bool = False,
    is_video: bool = False,
    user_id: int | None = None,
) -> bool:
    uid = user_id if user_id is not None else message.from_user.id
    model = get_model(uid)
    status_msg = None
    cancel_event: asyncio.Event | None = None

    try:
        image_data = await _download_telegram_file_id(file_id)

        if is_video:
            return await _do_generate_video(message, model, prompt, image_data, user_id=uid)

        reference_image = None
        if integrate_mode:
            reference_image, prereq_err = _validate_integrate_prerequisites(model, uid)
            if prereq_err:
                await message.answer(prereq_err, parse_mode="HTML")
                return False

        cancel_event = _start_job(uid, "edit")
        if cancel_event is None:
            await message.answer(_JOBS_FULL_MSG)
            return False
        status_label = (
            "Editando imagen (referencia+foto)..."
            if integrate_mode
            else f"Editando imagen con {model['name']}..."
        )
        status_msg = await message.answer(
            status_label,
            reply_markup=_cancel_job_keyboard(cancel_event),
        )
        if _job_cancelled(cancel_event):
            await status_msg.edit_text("⏹ Edición cancelada.", reply_markup=None)
            return True
        output, err, kie_meta = await generate_image(
            model,
            prompt,
            image_data,
            reference_image=reference_image,
            status_msg=status_msg,
            status_label=status_label,
        )
        if _job_cancelled(cancel_event):
            await status_msg.edit_text("⏹ Edición cancelada.", reply_markup=None)
            return True
        if err:
            await status_msg.edit_text(err, reply_markup=None)
            return False
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
        await process_image_result(
            output,
            prompt,
            status_msg,
            message,
            "Edit",
            download_allowlist=_download_allowlist_for_provider(model.get("provider")),
            kie_meta=kie_meta,
            regen_context=_build_image_regen_context(
                model=model,
                user_id=uid,
                prompt=prompt,
                mode="edit",
                source_file_id=file_id,
                integrate_mode=integrate_mode,
            ),
            model=model,
        )
        return True
    except replicate.exceptions.ReplicateError as e:
        backend = _prov_label(model.get("provider", "?"))
        if status_msg:
            await status_msg.edit_text(f"Error de {backend}: {e}", reply_markup=None)
        else:
            await message.answer(f"Error de {backend}: {e}")
        return False
    except Exception as e:
        if status_msg:
            await status_msg.edit_text(f"Error inesperado: {e}", reply_markup=None)
        else:
            await message.answer(f"Error inesperado: {e}")
        return False
    finally:
        if cancel_event is not None:
            _finish_job(uid, cancel_event)


async def _process_album_edit_from_file_ids(
    anchor_message: types.Message,
    prompt: str,
    file_ids: list[str],
    *,
    integrate_mode: bool = False,
    user_id: int | None = None,
) -> bool:
    uid = user_id if user_id is not None else anchor_message.from_user.id
    model = get_model(uid)
    n = len(file_ids)
    backend = _prov_label(model.get("provider", "?"))
    status_msg = None
    reference_image = None
    cancel_event = _start_job(uid, "album_edit")
    if cancel_event is None:
        await anchor_message.answer(_JOBS_FULL_MSG)
        return False
    completed = 0

    if integrate_mode:
        reference_image, prereq_err = _validate_integrate_prerequisites(model, uid)
        if prereq_err:
            _finish_job(uid, cancel_event)
            await anchor_message.answer(prereq_err, parse_mode="HTML")
            return False

    try:
        if integrate_mode:
            status_msg = await anchor_message.reply(
                f"Integrando referencia 0/{n} imágenes ({backend})...",
                reply_markup=_cancel_job_keyboard(cancel_event),
            )
        else:
            status_msg = await anchor_message.reply(
                f"Editando 0/{n} imágenes con {model['name']} ({backend})...",
                reply_markup=_cancel_job_keyboard(cancel_event),
            )

        for i, file_id in enumerate(file_ids, 1):
            if _job_cancelled(cancel_event):
                await status_msg.edit_text(
                    f"⏹ Cancelado. Completadas {completed}/{n} imágenes.",
                    reply_markup=None,
                )
                return True

            if integrate_mode:
                status_label = f"Integrando referencia {i}/{n} imágenes ({backend})..."
            else:
                status_label = f"Editando {i}/{n} imágenes con {model['name']} ({backend})..."
            await status_msg.edit_text(
                status_label,
                reply_markup=_cancel_job_keyboard(cancel_event),
            )
            image_data = await _download_telegram_file_id(file_id)
            if _job_cancelled(cancel_event):
                await status_msg.edit_text(
                    f"⏹ Cancelado. Completadas {completed}/{n} imágenes.",
                    reply_markup=None,
                )
                return True
            output, err, kie_meta = await generate_image(
                model,
                prompt,
                image_data,
                reference_image=reference_image,
                status_msg=status_msg,
                status_label=status_label,
            )
            if _job_cancelled(cancel_event):
                await status_msg.edit_text(
                    f"⏹ Cancelado. Completadas {completed}/{n} imágenes.",
                    reply_markup=None,
                )
                return True
            if err:
                await status_msg.edit_text(
                    f"{completed}/{n} completadas; error en imagen {i}: {err}",
                    reply_markup=None,
                )
                return False
            # Defensive/dead branch today: handle_album drops comfyui
            # media groups and the long-prompt path always collects a single photo,
            # so _process_album_edit_from_file_ids never routes comfyui albums in
            # production. Kept because it IS the correct routing if handle_album is
            # ever extended to route comfyui albums (then it's exercised end-to-end).
            if model.get("provider") == "comfyui":
                await _send_comfyui_output(
                    model,
                    output,
                    prompt,
                    status_msg,
                    anchor_message,
                    "Edit",
                    _build_image_regen_context(
                        model=model,
                        user_id=uid,
                        prompt=prompt,
                        mode="edit",
                        source_file_id=file_id,
                        integrate_mode=integrate_mode,
                    ),
                    delete_status=False,
                    meta=kie_meta,
                    cancel_event=cancel_event,
                )
            else:
                await process_image_result(
                    output,
                    prompt,
                    status_msg,
                    anchor_message,
                    "Edit",
                    delete_status=False,
                    download_allowlist=_download_allowlist_for_provider(model.get("provider")),
                    kie_meta=kie_meta,
                    regen_context=_build_image_regen_context(
                        model=model,
                        user_id=uid,
                        prompt=prompt,
                        mode="edit",
                        source_file_id=file_id,
                        integrate_mode=integrate_mode,
                    ),
                    model=model,
                )
            completed += 1
        else:
            await status_msg.edit_text(
                f"Completadas {n}/{n} imágenes.",
                reply_markup=None,
            )
        return True

    except replicate.exceptions.ReplicateError as e:
        if status_msg:
            await status_msg.edit_text(f"Error de {backend}: {e}", reply_markup=None)
        else:
            await anchor_message.answer(f"Error de {backend}: {e}")
        return False
    except Exception as e:
        if status_msg:
            await status_msg.edit_text(f"Error inesperado: {e}", reply_markup=None)
        else:
            await anchor_message.answer(f"Error inesperado: {e}")
        return False
    finally:
        _finish_job(uid, cancel_event)


async def _complete_long_prompt_collection(message: types.Message, prompt: str) -> bool:
    state = get_user_state(message.from_user.id)
    if not _is_awaiting_long_prompt_text(state):
        return False

    prompt_err = _validate_prompt(prompt)
    if prompt_err:
        await message.answer(prompt_err)
        return True

    file_ids = state.get("pending_edit_file_ids") or []
    if not file_ids:
        await message.answer(
            "No hay fotos guardadas para editar. Vuelve a enviar la imagen con caption largo."
        )
        return True

    integrate_mode = state.get("pending_edit_integrate_mode", False)
    is_video = state.get("pending_edit_is_video", False)

    success = False
    if is_video and len(file_ids) == 1:
        success = await _process_single_photo_edit(
            message,
            prompt,
            file_ids[0],
            integrate_mode=integrate_mode,
            is_video=True,
        )
    elif len(file_ids) == 1:
        success = await _process_single_photo_edit(
            message,
            prompt,
            file_ids[0],
            integrate_mode=integrate_mode,
        )
    else:
        success = await _process_album_edit_from_file_ids(
            message,
            prompt,
            file_ids,
            integrate_mode=integrate_mode,
        )

    if success:
        _clear_long_prompt_collection(state)
    return True


# ---------------------------------------------------------------------------
# /variables N — batch image editing with random pose/angle/action combos
# on the user's configured image model
# ---------------------------------------------------------------------------
def _is_variables_command(text: str | None) -> bool:
    """True when a caption/reply text is a /variables invocation.

    Requires a word boundary after 'variables' so captions like
    '/variablesfoo' are not hijacked from the normal edit path.
    """
    if not text:
        return False
    return re.match(r"^/variables(?:@|(?:\s|$))", text.strip(), re.IGNORECASE) is not None


def _parse_variables_count(text: str | None) -> int | None:
    """Parse '/variables N' → N clamped to [1, VARIABLES_MAX].

    Bare '/variables' → 1. Non-numeric or zero argument → None (invalid).
    """
    if not text:
        return None
    m = re.match(r"^/variables(?:@[A-Za-z0-9_]+)?(?:\s+(\d+))?\s*$", text.strip(), re.IGNORECASE)
    if not m:
        return None
    if m.group(1) is None:
        return 1
    n = int(m.group(1))
    if n < 1:
        return None
    return min(n, VARIABLES_MAX)


@dp.message(Command("variables"))
async def cmd_variables_help(message: types.Message):
    """'/variables' text command.

    aiogram's Command filter matches `text or caption`, and this handler is
    registered before handle_photo_caption/handle_reply_edit, so it must
    delegate photo captions and replies to the batch entry points; only bare
    text (no photo/reply) shows usage.
    """
    if message.reply_to_message:
        await cmd_variables_reply(message)
        return
    if message.photo and not (
        isinstance(message.media_group_id, str) and message.media_group_id
    ):
        await cmd_variables_photo(message)
        return
    await message.answer(
        "Para usar <b>/variables</b>, envía una foto con el caption "
        "<b>/variables N</b>, o responde a una foto con <b>/variables N</b>, "
        f"para generar N ediciones (N = 1-{VARIABLES_MAX}) combinando "
        "aleatoriamente poses, ángulos y acciones.\n\n"
        "Gestiona las listas con <b>/listas</b>.",
        parse_mode="HTML",
    )


def _variables_model_or_reject(uid: int) -> tuple[dict | None, str | None]:
    """Return (model, None) or (None, user-facing reject message)."""
    model = get_model(uid)
    if model.get("key") == "grok_video" or _comfyui_is_video(model):
        return None, (
            "El modelo de video no aplica para /variables; "
            "selecciona un modelo de imagen en /config."
        )
    if model.get("key") == "faceswap":
        return None, (
            "Face Swap no aplica para /variables; "
            "selecciona un modelo de imagen en /config."
        )
    provider = model.get("provider")
    if provider == "kie" and not KIE_API_KEY:
        return None, _KIE_NOT_CONFIGURED_MSG
    if provider == "comfyui" and not COMFYUI_HOST:
        return None, (
            "ComfyUI no configurado: agrega COMFYUI_HOST y COMFYUI_PORT "
            "al .env y reinicia el servicio."
        )
    if provider == "xai" and not XAI_API_KEY:
        return None, _XAI_NOT_CONFIGURED_MSG
    if provider == "replicate" and not REPLICATE_TOKEN:
        return None, _REPLICATE_NOT_CONFIGURED_MSG
    return model, None


def _variables_batch_summary(completed: int, failed: int, count: int) -> str:
    if failed == 0:
        return f"✅ Listo: {completed}/{count} imágenes generadas."
    err_label = "error" if failed == 1 else "errores"
    icon = "✅" if completed else "⚠️"
    return f"{icon} Listo: {completed}/{count} imágenes generadas ({failed} {err_label})."


async def _run_variables_batch(
    message: types.Message,
    count: int,
    image_data: BytesIO | None,
    kie_source_ref: dict | None,
    *,
    source_file_id: str | None = None,
) -> None:
    """Run `count` image edits on the user's configured image model.

    Always uses the original source image (never chains results), relaunching
    automatically after each result arrives. The batch is cancellable. A
    failed item is skipped and the remaining generations still run.
    """
    uid = message.from_user.id
    model, reject_msg = _variables_model_or_reject(uid)
    if reject_msg:
        await message.answer(reject_msg)
        return
    use_comfyui = model.get("provider") == "comfyui"

    lists = variables_store.get_lists()
    for name in variables_store.LIST_NAMES:
        if not lists[name]:
            await message.answer(
                f"La lista de <b>{variables_flow.LIST_LABELS[name]}</b> está vacía.\n"
                "Usa <b>/listas</b> para añadir opciones antes de usar /variables.",
                parse_mode="HTML",
            )
            return

    cancel_event = _start_job(uid, "variables")
    if cancel_event is None:
        await message.answer(_JOBS_FULL_MSG)
        return
    status_msg = None
    used_combos: set[tuple[str, str, str]] = set()
    completed = 0
    failed = 0
    try:
        status_msg = await message.answer(
            f"🎲 <b>Variables</b>: editando 0/{count} imágenes con {model['name']}...",
            parse_mode="HTML",
            reply_markup=_cancel_job_keyboard(cancel_event),
        )
        for i in range(1, count + 1):
            if _job_cancelled(cancel_event):
                await status_msg.edit_text(
                    f"⏹ Cancelado. Completadas {completed}/{count} imágenes.",
                    reply_markup=None,
                )
                return
            status_label = (
                f"🎲 <b>Variables</b>: editando {i}/{count} imágenes con {model['name']}..."
            )
            await status_msg.edit_text(
                status_label,
                parse_mode="HTML",
                reply_markup=_cancel_job_keyboard(cancel_event),
            )
            combo = variables_store.random_combination(exclude=used_combos)
            if combo is None:
                await status_msg.edit_text(
                    "No se pudo construir el prompt: alguna lista está vacía. Usa /listas.",
                    reply_markup=None,
                )
                return
            prompt, combo_tuple = combo
            used_combos.add(combo_tuple)

            if image_data is not None:
                image_data.seek(0)
            kie_ref = kie_source_ref if model.get("provider") == "kie" else None

            try:
                output, err, meta = await generate_image(
                    model,
                    prompt,
                    image_data,
                    kie_source_ref=kie_ref,
                    status_msg=status_msg,
                    status_label=status_label,
                    status_parse_mode="HTML",
                )
                if _job_cancelled(cancel_event):
                    await status_msg.edit_text(
                        f"⏹ Cancelado. Completadas {completed}/{count} imágenes.",
                        reply_markup=None,
                    )
                    return
                if err and meta and meta.get("exhausted"):
                    if image_data is not None:
                        image_data.seek(0)
                    shuffled_prompt = variables_store.build_prompt_shuffled(*combo_tuple)
                    output, err, meta = await generate_image(
                        model,
                        shuffled_prompt,
                        image_data,
                        kie_source_ref=kie_ref,
                        status_msg=status_msg,
                        status_label=status_label,
                        status_parse_mode="HTML",
                    )
                    if _job_cancelled(cancel_event):
                        await status_msg.edit_text(
                            f"⏹ Cancelado. Completadas {completed}/{count} imágenes.",
                            reply_markup=None,
                        )
                        return
                    if err and meta and meta.get("exhausted"):
                        variables_store.blacklist_add(variables_store.combo_key(*combo_tuple))
                        failed += 1
                        continue
                    prompt = shuffled_prompt
                if err:
                    failed += 1
                    continue
                regen_context = _build_image_regen_context(
                    model=model,
                    user_id=uid,
                    prompt=prompt,
                    mode="edit",
                    source_file_id=source_file_id,
                    kie_source_ref=kie_ref,
                )
                if use_comfyui:
                    await _send_comfyui_output(
                        model,
                        output,
                        prompt,
                        status_msg,
                        message,
                        f"Variables {i}/{count}",
                        regen_context,
                        delete_status=False,
                        meta=meta,
                        cancel_event=cancel_event,
                    )
                else:
                    await process_image_result(
                        output,
                        prompt,
                        status_msg,
                        message,
                        f"Variables {i}/{count}",
                        download_allowlist=_download_allowlist_for_provider(model.get("provider")),
                        kie_meta=meta,
                        regen_context=regen_context,
                        delete_status=False,
                        model=model,
                    )
            except Exception:
                failed += 1
                continue
            completed += 1

        await status_msg.edit_text(
            _variables_batch_summary(completed, failed, count),
            reply_markup=None,
        )
    except Exception as e:
        if status_msg:
            await status_msg.edit_text(f"Error inesperado: {e}", reply_markup=None)
        else:
            await message.answer(f"Error inesperado: {e}")
    finally:
        _finish_job(uid, cancel_event)


async def cmd_variables_photo(message: types.Message) -> None:
    """Photo sent with a '/variables N' caption."""
    count = _parse_variables_count(message.caption)
    if count is None:
        await message.answer(
            f"Uso: envía la foto con el caption <b>/variables N</b> (N = 1-{VARIABLES_MAX}).\n\n"
            "Gestiona las listas con <b>/listas</b>.",
            parse_mode="HTML",
        )
        return
    image_data = await _download_telegram_file_id(message.photo[-1].file_id)
    await _run_variables_batch(
        message,
        count,
        image_data,
        None,
        source_file_id=message.photo[-1].file_id,
    )


async def cmd_variables_reply(message: types.Message) -> None:
    """'/variables N' sent as a reply to a photo."""
    count = _parse_variables_count(message.text or message.caption)
    if count is None:
        await message.answer(
            f"Uso: responde a una foto con <b>/variables N</b> (N = 1-{VARIABLES_MAX}).\n\n"
            "Gestiona las listas con <b>/listas</b>.",
            parse_mode="HTML",
        )
        return
    if not message.reply_to_message or not message.reply_to_message.photo:
        await message.answer("Responde a una foto para editarla con /variables.")
        return
    kie_source_ref = None
    image_data = None
    source_file_id = None
    if get_model(message.from_user.id).get("provider") == "kie":
        kie_source_ref = _resolve_reply_kie_ref(message.reply_to_message)
    if kie_source_ref is None:
        image_data = await _download_telegram_photo(message.reply_to_message.photo[-1])
        source_file_id = message.reply_to_message.photo[-1].file_id
    await _run_variables_batch(
        message,
        count,
        image_data,
        kie_source_ref,
        source_file_id=source_file_id,
    )


# ---------------------------------------------------------------------------
# PHOTO + CAPTION  — route by model
# ---------------------------------------------------------------------------
@dp.message(lambda m: m.photo and m.caption and not m.media_group_id)
async def handle_photo_caption(message: types.Message):
    if isinstance(message.media_group_id, str) and message.media_group_id:
        return

    # --- variables: photo + '/variables N' caption → random combo batch edit ---
    if _is_variables_command(message.caption):
        await cmd_variables_photo(message)
        return

    state = get_user_state(message.from_user.id)

    if state.get("integrate_ref_awaiting"):
        await _handle_integrate_ref_photo(message)
        return

    # --- faceswap: photo + caption (caption ignored, just do swap) ---
    if state["model"] == "faceswap":
        await _handle_faceswap_photo(message)
        return

    # --- grok / grok_video / seedream / comfyui: photo + caption → edit ---
    integrate_mode, prompt = _parse_integrate_caption(message.caption)
    if _prompt_needs_long_text_collection(prompt):
        model = get_model(message.from_user.id)
        is_video = model["key"] == "grok_video"
        _set_long_prompt_collection(
            state,
            file_ids=[message.photo[-1].file_id],
            integrate_mode=integrate_mode,
            is_video=is_video,
        )
        await _long_prompt_collection_reply(message, is_video=is_video, n_photos=1)
        return

    prompt_err = _validate_prompt(prompt)
    if prompt_err:
        await message.answer(prompt_err)
        return

    _clear_long_prompt_collection(state)
    model = get_model(message.from_user.id)
    await _process_single_photo_edit(
        message,
        prompt,
        message.photo[-1].file_id,
        integrate_mode=integrate_mode,
        is_video=(model["key"] == "grok_video"),
    )


# ---------------------------------------------------------------------------
# PHOTO WITHOUT CAPTION  — route by model
# ---------------------------------------------------------------------------
@dp.message(lambda m: m.photo and not m.caption and not m.media_group_id)
async def handle_photo_no_caption(message: types.Message):
    state = get_user_state(message.from_user.id)

    if state.get("integrate_ref_awaiting"):
        await _handle_integrate_ref_photo(message)
        return

    if state["model"] == "faceswap":
        await _handle_faceswap_photo(message)
        return

    if _is_awaiting_long_prompt_text(state):
        await message.answer(
            "Tienes una edición pendiente. Envíame el prompt como <b>mensaje de texto</b> "
            "(no hace falta responder a ningún mensaje).",
            parse_mode="HTML",
        )
        return

    # grok / grok_video / seedream
    if get_model(message.from_user.id)["key"] == "grok_video":
        await message.answer(
            "Para animar una imagen (imagen a video), enviala con un <b>caption</b> describiendo el movimiento.\n\n"
            "Ejemplo: envia tu foto con el texto <i>\"haz que el agua caiga y aleja la camara lentamente\"</i>",
            parse_mode="HTML",
        )
        return

    await message.answer(
        "Para editar una imagen, enviala con un <b>caption</b> describiendo los cambios que quieres.\n\n"
        "Ejemplo: envia tu foto con el texto <i>\"cambia el fondo a una playa al atardecer\"</i>",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# REPLY messages — long-prompt collection or photo edit
# ---------------------------------------------------------------------------
@dp.message(lambda m: m.text and m.reply_to_message)
async def handle_reply_edit(message: types.Message):
    # Defensive: never let /listas panel text-input be intercepted by the
    # reply-edit flow (the FSM handlers normally catch it first).
    if await _delegate_variables_input(message):
        return

    state = get_user_state(message.from_user.id)

    # --- variables: '/variables N' replied to a photo → random combo batch ---
    if _is_variables_command(message.text):
        await cmd_variables_reply(message)
        return

    if _is_awaiting_long_prompt_text(state):
        await _complete_long_prompt_collection(message, message.text.strip())
        return

    if not message.reply_to_message.photo:
        return

    if state["model"] == "faceswap":
        await message.answer(
            "En modo Face Swap no se usa reply con texto.\n"
            "Simplemente envia la foto directamente para hacer el swap.",
        )
        return

    # grok / grok_video / seedream
    prompt = message.text.strip()
    prompt_err = _validate_prompt(prompt)
    if prompt_err:
        await message.answer(prompt_err)
        return

    model = get_model(message.from_user.id)
    status_msg = None

    try:
        kie_source_ref = None
        image_data = None
        if model.get("provider") == "kie":
            kie_source_ref = _resolve_reply_kie_ref(message.reply_to_message)
        if kie_source_ref is None:
            image_data = await _download_telegram_photo(message.reply_to_message.photo[-1])

        if model["key"] == "grok_video":
            await _do_generate_video(
                message,
                model,
                prompt,
                image_data,
                user_id=message.from_user.id,
                kie_source_ref=kie_source_ref,
            )
            return

        status_label = f"Editando imagen con {model['name']}..."
        status_msg = await message.answer(status_label)
        backend = _prov_label(model.get("provider", "?"))
        output, err, kie_meta = await generate_image(
            model,
            prompt,
            image_data,
            kie_source_ref=kie_source_ref,
            status_msg=status_msg,
            status_label=status_label,
        )
        if err:
            await status_msg.edit_text(err)
            return
        if model.get("provider") == "comfyui":
            src = message.reply_to_message.photo[-1].file_id if message.reply_to_message.photo else None
            await _send_comfyui_output(
                model,
                output,
                prompt,
                status_msg,
                message,
                "Edit",
                _build_image_regen_context(
                    model=model,
                    user_id=message.from_user.id,
                    prompt=prompt,
                    mode="edit",
                    source_file_id=src,
                ),
                meta=kie_meta,
                cancel_event=None,
            )
            return
        source_file_id = None
        if kie_source_ref is None and message.reply_to_message.photo:
            source_file_id = message.reply_to_message.photo[-1].file_id
        await process_image_result(
            output,
            prompt,
            status_msg,
            message,
            "Edit",
            download_allowlist=_download_allowlist_for_provider(model.get("provider")),
            kie_meta=kie_meta,
            regen_context=_build_image_regen_context(
                model=model,
                user_id=message.from_user.id,
                prompt=prompt,
                mode="edit",
                source_file_id=source_file_id,
                kie_source_ref=kie_source_ref,
            ),
            model=model,
        )
    except replicate.exceptions.ReplicateError as e:
        backend = _prov_label(model.get("provider", "?"))
        if status_msg:
            await status_msg.edit_text(f"Error de {backend}: {e}")
        else:
            await message.answer(f"Error de {backend}: {e}")
    except Exception as e:
        if status_msg:
            await status_msg.edit_text(f"Error inesperado: {e}")
        else:
            await message.answer(f"Error inesperado: {e}")


# ---------------------------------------------------------------------------
# ALBUM (media group) — faceswap batch swap; grok sequential i2i edit
# ---------------------------------------------------------------------------
_album_cache: dict[tuple, list] = {}
_album_lock = asyncio.Lock()
ALBUM_COLLECT_DELAY = 1.0


def _album_prompt(messages: list[types.Message]) -> str | None:
    for msg in sorted(messages, key=lambda m: m.message_id):
        if msg.caption and msg.caption.strip():
            return msg.caption.strip()
    return None


@dp.message(lambda m: m.photo and m.media_group_id)
async def handle_album(message: types.Message):
    state = get_user_state(message.from_user.id)
    model_key = state["model"]

    if state.get("integrate_ref_awaiting"):
        await _handle_integrate_ref_photo(message)
        return

    if model_key not in ("faceswap", "grok"):
        return

    media_group_id = message.media_group_id
    chat_id = message.chat.id
    cache_key = (chat_id, media_group_id)

    if model_key == "faceswap":
        if state["fs_state"] == sessions.FsState.AWAITING_SOURCE:
            await _handle_faceswap_source_photo(message)
            return

        if not state["source_path"]:
            await message.answer(
                "Primero configura tu cara fuente con /cambiar_source."
            )
            return

        async with _album_lock:
            if cache_key not in _album_cache:
                _album_cache[cache_key] = []
                asyncio.create_task(
                    _process_album_after_delay(cache_key, chat_id, message)
                )
            _album_cache[cache_key].append(message)
        return

    async with _album_lock:
        if cache_key not in _album_cache:
            _album_cache[cache_key] = []
            asyncio.create_task(
                _process_grok_album_after_delay(cache_key, message)
            )
        _album_cache[cache_key].append(message)


async def _process_grok_album_after_delay(cache_key: tuple, first_msg: types.Message):
    await asyncio.sleep(ALBUM_COLLECT_DELAY)

    async with _album_lock:
        messages = _album_cache.pop(cache_key, [])

    if not messages:
        return

    messages = sorted(messages, key=lambda m: m.message_id)
    n = len(messages)
    if n > INTEGRATE_MAX_ALBUM:
        await first_msg.answer(
            f"El album tiene {n} fotos; el maximo es {INTEGRATE_MAX_ALBUM}."
        )
        return

    raw_caption = _album_prompt(messages)
    if not raw_caption:
        await first_msg.answer(
            "Para editar una imagen, enviala con un <b>caption</b> describiendo los cambios que quieres.\n\n"
            "Ejemplo: envia tu foto con el texto <i>\"cambia el fondo a una playa al atardecer\"</i>",
            parse_mode="HTML",
        )
        return

    integrate_mode, prompt = _parse_integrate_caption(raw_caption)
    if _prompt_needs_long_text_collection(prompt):
        file_ids = [msg.photo[-1].file_id for msg in messages if msg.photo]
        state = get_user_state(first_msg.from_user.id)
        _set_long_prompt_collection(
            state,
            file_ids=file_ids,
            integrate_mode=integrate_mode,
            is_video=False,
        )
        await _long_prompt_collection_reply(
            first_msg,
            is_video=False,
            n_photos=len(file_ids),
        )
        return

    prompt_err = _validate_prompt(prompt)
    if prompt_err:
        await first_msg.answer(prompt_err)
        return

    state = get_user_state(first_msg.from_user.id)
    _clear_long_prompt_collection(state)
    file_ids = [msg.photo[-1].file_id for msg in messages if msg.photo]
    await _process_album_edit_from_file_ids(
        first_msg,
        prompt,
        file_ids,
        integrate_mode=integrate_mode,
    )


async def _process_album_after_delay(cache_key: tuple, chat_id: int, first_msg: types.Message):
    await asyncio.sleep(ALBUM_COLLECT_DELAY)

    async with _album_lock:
        messages = _album_cache.pop(cache_key, [])

    if not messages:
        return

    state = get_user_state(first_msg.from_user.id)
    source_path = Path(state["source_path"])
    if not source_path.exists():
        await first_msg.reply("Source no encontrado. Usa /cambiar_source.")
        return

    file_ids = [msg.photo[-1].file_id for msg in messages if msg.photo]
    if not file_ids:
        return

    await _request_faceswap_confirmation(first_msg, file_ids)


# ---------------------------------------------------------------------------
# Face swap photo processing (single photo, no album)
# ---------------------------------------------------------------------------
async def _request_faceswap_confirmation(message: types.Message, file_ids: list[str]) -> None:
    state = get_user_state(message.from_user.id)
    state["pending_prompt"] = None
    state["pending_faceswap_file_ids"] = file_ids
    n = len(file_ids)
    if n == 1:
        text = "¿Confirmas hacer face swap con esta imagen?"
    else:
        text = f"¿Confirmas hacer face swap con estas {n} imágenes?"
    await message.answer(text, reply_markup=_confirmation_keyboard())


_replicate_client: replicate.Client | None = None


def _get_replicate_client() -> replicate.Client:
    global _replicate_client
    if _replicate_client is None:
        _replicate_client = replicate.Client(timeout=REPLICATE_HTTP_TIMEOUT)
    return _replicate_client


def _urllib_download_bytes(url: str, *, timeout: int = DOWNLOAD_TIMEOUT_SEC) -> bytes:
    import urllib.request

    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read()


def _replicate_output_bytes(output) -> bytes:
    if hasattr(output, "read"):
        data = output.read()
        if isinstance(data, bytes):
            return data
    if isinstance(output, str):
        return _urllib_download_bytes(output)
    if isinstance(output, list) and output:
        item = output[0]
        if hasattr(item, "read"):
            data = item.read()
            if isinstance(data, bytes):
                return data
        url = item.url if hasattr(item, "url") else str(item)
        return _urllib_download_bytes(url)
    raise ValueError("Formato de salida de Replicate no reconocido")


def _faceswap_replicate_single(
    source_path: str | Path,
    target_path: Path,
    output_dir: Path,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    client = _get_replicate_client()
    # cdingram/face-swap: swap_image = face to insert, input_image = target scene
    with open(source_path, "rb") as src_f, open(target_path, "rb") as tgt_f:
        output = client.run(
            MODELS["faceswap"]["id"],
            input={"swap_image": src_f, "input_image": tgt_f},
            wait=REPLICATE_WAIT_SEC,
        )
    result_path = output_dir / target_path.name
    result_path.write_bytes(_replicate_output_bytes(output))
    return result_path


def _faceswap_progress_bar(
    completed: int,
    total: int,
    *,
    width: int = FACESWAP_PROGRESS_WIDTH,
) -> str:
    if total <= 0:
        return f"[{'?' * width}] 0/0 (0%)"
    completed = max(0, min(completed, total))
    filled = round(width * completed / total)
    empty = width - filled
    pct = completed * 100 // total
    return f"[{'█' * filled}{'░' * empty}] {completed}/{total} ({pct}%)"


def _faceswap_progress_message(
    completed: int,
    total: int,
    *,
    current: int | None = None,
) -> str:
    bar = _faceswap_progress_bar(completed, total)
    if current is not None and 1 <= current <= total:
        return f"Face swap\n{bar}\nImagen {current}/{total} en Replicate..."
    return f"Face swap\n{bar}"


def _log_faceswap_progress(
    user_id: int,
    completed: int,
    total: int,
    *,
    current: int | None = None,
    target_name: str | None = None,
    outcome: str | None = None,
    detail: str | None = None,
) -> None:
    parts = [
        f"[faceswap] user={user_id}",
        _faceswap_progress_bar(completed, total),
    ]
    if current is not None:
        parts.append(f"step={current}/{total}")
    if target_name:
        parts.append(f"file={target_name}")
    if outcome:
        parts.append(f"outcome={outcome}")
    if detail:
        parts.append(f"detail={detail}")
    print(" ".join(parts))


def _format_faceswap_batch_status(
    processed: int,
    total: int,
    failures: list[str],
    *,
    cancelled: bool = False,
) -> str:
    bar = _faceswap_progress_bar(processed, total)
    if cancelled:
        summary = f"⏹ Cancelado. Completadas {processed}/{total} imagenes."
        if failures:
            shown = failures[:3]
            detail = "; ".join(shown)
            if len(failures) > 3:
                detail += f"; y {len(failures) - 3} mas"
            return f"{bar}\n{summary}\nFallos: {detail}"
        return f"{bar}\n{summary}"
    if failures:
        summary = f"Completadas {processed}/{total} imagenes."
        if processed == 0:
            summary = f"No se pudo procesar ninguna de las {total} imagenes."
        shown = failures[:3]
        detail = "; ".join(shown)
        if len(failures) > 3:
            detail += f"; y {len(failures) - 3} mas"
        return f"{bar}\n{summary}\nFallos: {detail}"
    if total == 1:
        return f"{bar}\nProcesada 1 imagen."
    return f"{bar}\nProcesadas {processed}/{total} imagenes."


async def _send_faceswap_photos(
    anchor_message: types.Message,
    result_paths: list[Path],
) -> None:
    if not result_paths:
        return
    for offset in range(0, len(result_paths), TELEGRAM_MEDIA_GROUP_MAX):
        chunk = result_paths[offset:offset + TELEGRAM_MEDIA_GROUP_MAX]
        media = [
            types.InputMediaPhoto(
                media=BufferedInputFile(path.read_bytes(), filename=path.name)
            )
            for path in chunk
        ]
        await anchor_message.reply_media_group(media)


async def _execute_faceswap_single(
    anchor_message: types.Message,
    file_id: str,
    *,
    user_id: int,
    status_msg: types.Message | None = None,
) -> None:
    state = get_user_state(user_id)
    source_path = Path(state["source_path"])
    if not source_path.exists():
        text = "Source no encontrado. Usa /cambiar_source para configurar de nuevo."
        if status_msg:
            await safe_edit_text(status_msg, text, reply_markup=None)
        else:
            await anchor_message.answer(text)
        state["source_path"] = None
        return

    cancel_event = _start_job(user_id, "faceswap")
    if cancel_event is None:
        if status_msg:
            await safe_edit_text(status_msg, _JOBS_FULL_MSG, reply_markup=None)
        else:
            await anchor_message.answer(_JOBS_FULL_MSG)
        return
    progress_text = _faceswap_progress_message(0, 1, current=1)
    if status_msg is None:
        status_msg = await anchor_message.answer(
            progress_text,
            reply_markup=_cancel_job_keyboard(cancel_event),
        )
    else:
        await safe_edit_text(
            status_msg,
            progress_text,
            reply_markup=_cancel_job_keyboard(cancel_event),
        )

    temp_input = Path(tempfile.mkdtemp(prefix="fs_single_"))
    temp_output = temp_input / "output"
    target_path = None
    result_path = None

    try:
        if _job_cancelled(cancel_event):
            await safe_edit_text(
                status_msg,
                _format_faceswap_batch_status(0, 1, [], cancelled=True),
                reply_markup=None,
            )
            return

        _log_faceswap_progress(user_id, 0, 1, current=1, outcome="start")
        target_path = await download.download_telegram_photo(bot, file_id, temp_input)
        if _job_cancelled(cancel_event):
            await safe_edit_text(
                status_msg,
                _format_faceswap_batch_status(0, 1, [], cancelled=True),
                reply_markup=None,
            )
            return
        result_path = await asyncio.to_thread(
            _faceswap_replicate_single,
            source_path,
            target_path,
            temp_output,
        )
        if _job_cancelled(cancel_event):
            await safe_edit_text(
                status_msg,
                _format_faceswap_batch_status(0, 1, [], cancelled=True),
                reply_markup=None,
            )
            return
        await _send_faceswap_photos(anchor_message, [result_path])
        _log_faceswap_progress(
            user_id,
            1,
            1,
            target_name=target_path.name,
            outcome="ok",
        )
        await safe_edit_text(
            status_msg,
            _format_faceswap_batch_status(1, 1, []),
            reply_markup=None,
        )
    except Exception as e:
        _log_faceswap_progress(
            user_id,
            0,
            1,
            current=1,
            target_name=target_path.name if target_path else None,
            outcome="fail",
            detail=str(e),
        )
        await safe_edit_text(
            status_msg,
            _format_faceswap_batch_status(0, 1, [str(e)]),
            reply_markup=None,
        )
    finally:
        _finish_job(user_id, cancel_event)
        if target_path:
            download.cleanup_temp_files([target_path])
        shutil.rmtree(temp_input, ignore_errors=True)


async def _execute_faceswap_batch(
    anchor_message: types.Message,
    file_ids: list[str],
    *,
    user_id: int,
    status_msg: types.Message | None = None,
) -> None:
    state = get_user_state(user_id)
    source_path = Path(state["source_path"])
    if not source_path.exists():
        text = "Source no encontrado. Usa /cambiar_source."
        if status_msg:
            await safe_edit_text(status_msg, text, reply_markup=None)
        else:
            await anchor_message.answer(text)
        state["source_path"] = None
        return

    count = len(file_ids)
    cancel_event = _start_job(user_id, "faceswap")
    if cancel_event is None:
        if status_msg:
            await safe_edit_text(status_msg, _JOBS_FULL_MSG, reply_markup=None)
        else:
            await anchor_message.answer(_JOBS_FULL_MSG)
        return
    initial_status = _faceswap_progress_message(0, count, current=1 if count else None)
    if status_msg is None:
        status_msg = await anchor_message.reply(
            initial_status,
            reply_markup=_cancel_job_keyboard(cancel_event),
        )
    else:
        await safe_edit_text(
            status_msg,
            initial_status,
            reply_markup=_cancel_job_keyboard(cancel_event),
        )

    temp_root = Path(tempfile.mkdtemp(prefix="fs_album_"))
    temp_output = temp_root / "output"
    downloaded: list[Path] = []
    result_paths: list[Path] = []
    failures: list[str] = []
    cancelled = False

    try:
        for i, file_id in enumerate(file_ids, 1):
            if _job_cancelled(cancel_event):
                cancelled = True
                _log_faceswap_progress(
                    user_id,
                    len(result_paths),
                    count,
                    current=i,
                    outcome="cancelled",
                )
                break

            await safe_edit_text(
                status_msg,
                _faceswap_progress_message(i - 1, count, current=i),
                reply_markup=_cancel_job_keyboard(cancel_event),
            )
            _log_faceswap_progress(user_id, i - 1, count, current=i, outcome="start")
            target_path = None
            try:
                target_path = await download.download_telegram_photo(
                    bot, file_id, temp_root / "input"
                )
                downloaded.append(target_path)
                if _job_cancelled(cancel_event):
                    cancelled = True
                    break
                result_path = await asyncio.to_thread(
                    _faceswap_replicate_single,
                    source_path,
                    target_path,
                    temp_output,
                )
                if _job_cancelled(cancel_event):
                    # Drop this result if cancelled during the API wait.
                    cancelled = True
                    break
                result_paths.append(result_path)
                await safe_edit_text(
                    status_msg,
                    _faceswap_progress_message(i, count),
                    reply_markup=_cancel_job_keyboard(cancel_event),
                )
                _log_faceswap_progress(
                    user_id,
                    i,
                    count,
                    target_name=target_path.name,
                    outcome="ok",
                )
            except Exception as e:
                failures.append(f"imagen {i}: {e}")
                _log_faceswap_progress(
                    user_id,
                    len(result_paths),
                    count,
                    current=i,
                    target_name=target_path.name if target_path else None,
                    outcome="fail",
                    detail=str(e),
                )
            finally:
                if not cancelled and i < count and not _job_cancelled(cancel_event):
                    await asyncio.sleep(REPLICATE_RATE_LIMIT_SEC)
                elif _job_cancelled(cancel_event):
                    cancelled = True

        send_error = None
        if result_paths:
            try:
                await _send_faceswap_photos(anchor_message, result_paths)
            except Exception as e:
                send_error = str(e)
                print(f"[faceswap] media group error: {e}")
                for result_path in result_paths:
                    try:
                        photo = BufferedInputFile(
                            result_path.read_bytes(),
                            filename=result_path.name,
                        )
                        await anchor_message.reply_photo(photo)
                    except Exception as photo_exc:
                        print(f"[faceswap] fallback photo error: {photo_exc}")
                        if send_error is None:
                            send_error = str(photo_exc)

        if send_error:
            failures.append(f"envio a Telegram: {send_error}")

        await safe_edit_text(
            status_msg,
            _format_faceswap_batch_status(
                len(result_paths),
                count,
                failures,
                cancelled=cancelled,
            ),
            reply_markup=None,
        )
    except Exception as e:
        print(f"[faceswap] batch unexpected error: {e}")
        if result_paths:
            try:
                await _send_faceswap_photos(anchor_message, result_paths)
            except Exception as send_exc:
                print(f"[faceswap] rescue send error: {send_exc}")
        await safe_edit_text(
            status_msg,
            _format_faceswap_batch_status(
                len(result_paths),
                count,
                failures or [str(e)],
                cancelled=cancelled or _job_cancelled(cancel_event),
            ),
            reply_markup=None,
        )
    finally:
        _finish_job(user_id, cancel_event)
        download.cleanup_temp_files(downloaded)
        shutil.rmtree(temp_root, ignore_errors=True)


async def _handle_faceswap_photo(message: types.Message):
    state = get_user_state(message.from_user.id)

    # If awaiting source, save as source
    if state["fs_state"] == sessions.FsState.AWAITING_SOURCE:
        await _handle_faceswap_source_photo(message)
        return

    # Need a source to swap
    if not state["source_path"]:
        await message.answer(
            "Primero configura tu cara fuente con /cambiar_source."
        )
        return

    source_path = Path(state["source_path"])
    if not source_path.exists():
        await message.answer(
            "Source no encontrado. Usa /cambiar_source para configurar de nuevo."
        )
        state["source_path"] = None
        return

    await _request_faceswap_confirmation(message, [message.photo[-1].file_id])


async def _handle_faceswap_source_photo(message: types.Message):
    file_id = message.photo[-1].file_id
    SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    source_path = SOURCES_DIR / f"{message.from_user.id}.jpg"

    file = await bot.get_file(file_id)
    file_bytes = await bot.download_file(file.file_path)
    source_path.write_bytes(file_bytes.read())

    state = get_user_state(message.from_user.id)
    state["source_path"] = str(source_path)
    state["fs_state"] = sessions.FsState.IDLE
    sessions.set_source(message.from_user.id, str(source_path))

    await message.answer("Source actualizado. Ahora envia tus fotos para hacer face swap.")


async def _handle_integrate_ref_photo(message: types.Message):
    file_id = message.photo[-1].file_id
    INTEGRATE_REFS_DIR.mkdir(parents=True, exist_ok=True)
    ref_path = INTEGRATE_REFS_DIR / f"{message.from_user.id}.jpg"

    file = await bot.get_file(file_id)
    file_bytes = await bot.download_file(file.file_path)
    ref_path.write_bytes(file_bytes.read())

    state = get_user_state(message.from_user.id)
    state["integrate_ref_path"] = str(ref_path)
    state["integrate_ref_awaiting"] = False
    sessions.set_integrate_ref(message.from_user.id, str(ref_path))

    await message.answer(
        "Referencia actualizada. En un album o foto con caption que empiece por "
        "<b>/s</b>, cada imagen se editara junto con esta referencia.",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# Replicate face swap batch (sync wrapper for CLI/tests)
# ---------------------------------------------------------------------------
def _process_batch_replicate_sync(source_path: str, input_dir: Path, output_dir: Path) -> dict:
    from tqdm import tqdm

    extensions = (".jpg", ".jpeg", ".png", ".webp")
    image_files = []
    for ext in extensions:
        image_files.extend(list(input_dir.glob(f"*{ext}")))
        image_files.extend(list(input_dir.glob(f"*{ext.upper()}")))
    image_files = sorted(set(image_files))

    stats = {"total": len(image_files), "processed": 0, "failed": 0}
    for index, target_path in enumerate(tqdm(image_files, desc="Face swap", unit="img")):
        try:
            _faceswap_replicate_single(source_path, target_path, output_dir)
            stats["processed"] += 1
            print(
                f"[faceswap] {_faceswap_progress_bar(stats['processed'], stats['total'])} "
                f"file={target_path.name} outcome=ok"
            )
        except Exception as e:
            print(
                f"[faceswap] {_faceswap_progress_bar(stats['processed'], stats['total'])} "
                f"file={target_path.name} outcome=fail detail={e}"
            )
            stats["failed"] += 1
        if index < len(image_files) - 1:
            time.sleep(REPLICATE_RATE_LIMIT_SEC)

    return stats


# ---------------------------------------------------------------------------
# Image generation / editing (grok / seedream)
# ---------------------------------------------------------------------------
async def generate_image(
    model: dict,
    prompt: str,
    image_data: BytesIO | None = None,
    *,
    reference_image: BytesIO | None = None,
    kie_source_ref: dict | None = None,
    status_msg: types.Message | None = None,
    status_label: str = "",
    status_parse_mode: str | None = None,
) -> tuple[object | None, str | None, dict | None]:
    prov = model.get("provider", "?")
    model_id = model.get("id")
    print(
        f"[generate] key={model.get('key')} provider={prov} id={model_id} "
        f"has_image={image_data is not None} has_ref={reference_image is not None} "
        f"kie_ref={kie_source_ref is not None}"
    )
    last_err: str | None = None
    for attempt in range(GENERATE_MAX_RETRIES + 1):
        if attempt > 0:
            print(f"[generate] retry attempt {attempt}/{GENERATE_MAX_RETRIES} provider={prov}")
        if image_data is not None:
            image_data.seek(0)
        try:
            output, err, meta = await _generate_once(
                model,
                prompt,
                image_data,
                reference_image=reference_image,
                kie_source_ref=kie_source_ref,
            )
        except Exception as exc:
            output, err, meta = None, f"Error de {_prov_label(prov)}: {exc}", None
        if err is None:
            return output, None, meta
        if meta and meta.get("retryable") is False:
            return None, err, meta
        last_err = err
        if attempt < GENERATE_MAX_RETRIES:
            await _update_retry_status(status_msg, status_label, status_parse_mode, attempt)
            await asyncio.sleep(_retry_backoff(attempt))
    return None, last_err, {"exhausted": True, "provider": prov}


async def _generate_once(
    model: dict,
    prompt: str,
    image_data: BytesIO | None = None,
    *,
    reference_image: BytesIO | None = None,
    kie_source_ref: dict | None = None,
) -> tuple[object | None, str | None, dict | None]:
    """Single generation attempt against the provider selected by model["provider"]."""
    prov = model.get("provider", "?")
    if prov == "xai":
        output, err = await _generate_xai(
            model, prompt, image_data, reference_image=reference_image
        )
        return output, err, None
    if prov == "kie":
        return await _generate_kie_once(
            model,
            prompt,
            image_data,
            kie_source_ref=kie_source_ref,
        )
    if prov == "comfyui":
        locals_, err, meta = await _generate_comfyui(model, prompt, image_data)
        return locals_, err, meta
    output, err = await _generate_replicate(model, prompt, image_data)
    return output, err, None


async def _generate_replicate(model: dict, prompt: str, image_data: BytesIO | None = None) -> tuple[object | None, str | None]:
    model_id = model["id"]
    input_data: dict = {"prompt": prompt}
    extra_kwargs: dict = {}

    if image_data:
        if model["key"] == "seedream":
            image_data.seek(0)
            input_data["image_input"] = [_image_to_data_uri(image_data)]
            input_data["size"] = "2K"
        else:
            input_data["image"] = image_data
            extra_kwargs["file_encoding_strategy"] = "base64"
    elif model["key"] == "grok":
        input_data["aspect_ratio"] = sessions.DEFAULT_IMAGE_ASPECT_RATIO

    output = await asyncio.to_thread(replicate.run, model_id, input=input_data, **extra_kwargs)
    return output, None


# ---------------------------------------------------------------------------
# ComfyUI provider — generation on the user's Vast GPU box (SSH + native API)
# ---------------------------------------------------------------------------
def _comfyui_ssh_base() -> tuple[str, int | None, str | None]:
    if not COMFYUI_HOST:
        return "", None, (
            "ComfyUI no configurado: agrega COMFYUI_HOST y COMFYUI_PORT "
            "al .env y reinicia el servicio."
        )
    try:
        port = int(COMFYUI_PORT or 22)
    except ValueError:
        return "", None, "COMFYUI_PORT inválido en .env."
    return (
        f"ssh -p {port} -o BatchMode=yes -o ConnectTimeout=25 root@{COMFYUI_HOST}",
        port,
        None,
    )


async def _comfyui_run_remote(cmd: str, prompt: str, *, timeout: int = 600) -> tuple[list[str], int | None]:
    """Run gen_comfy.py on the box with the prompt on stdin.

    Returns (output_paths, returncode) — multi-image batches yield one path per
    output. returncode is None when the run never completed (ssh unavailable or
    timeout), otherwise the box's exit code (2 = invalid refine config, 3 = no
    output produced) so callers can surface a specific error."""
    ssh_base, _port, err = _comfyui_ssh_base()
    if err or not ssh_base:
        return [], None
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
        return [], None
    out = (proc.stdout or b"").decode(errors="replace").strip()
    lines = [l for l in out.splitlines() if l.startswith("/workspace")]
    return lines, proc.returncode


def _comfyui_tmpdir() -> str:
    """Directorio local para pulls/uploads de ComfyUI — fuera de /tmp (tmpfs con
    cuota en este servidor: EDQUOT rompía el scp de vuelta)."""
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tmp", "comfyui")
    os.makedirs(d, exist_ok=True)
    return d


async def _comfyui_pull(remote_path: str) -> str:
    """scp the generated file to a local temp path. Returns local path or ''."""
    ssh_base, port, err = _comfyui_ssh_base()
    if err or not ssh_base:
        return ""
    ext = os.path.splitext(remote_path)[1] or ".png"
    local = os.path.join(
        _comfyui_tmpdir(), f"comfyui_{int(time.time())}_{uuid.uuid4().hex[:6]}{ext}"
    )
    proc = await asyncio.to_thread(
        subprocess.run,
        [
            "scp", "-P", str(port), "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=25", f"root@{COMFYUI_HOST}:{remote_path}", local,
        ],
        capture_output=True,
        timeout=120,
    )
    if proc.returncode != 0 or not os.path.exists(local):
        return ""
    return local


async def _comfyui_upload(image_data: BytesIO) -> str:
    """Upload a Telegram photo to the box's ComfyUI input dir. Returns remote filename or ''."""
    ssh_base, port, err = _comfyui_ssh_base()
    if err or not ssh_base:
        return ""
    name = f"edit_{int(time.time())}_{uuid.uuid4().hex[:6]}.png"
    local = os.path.join(_comfyui_tmpdir(), name)
    with open(local, "wb") as f:
        image_data.seek(0)
        f.write(image_data.read())
    proc = await asyncio.to_thread(
        subprocess.run,
        [
            "scp", "-P", str(port), "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=25", local,
            f"root@{COMFYUI_HOST}:/workspace/ComfyUI/input/{name}",
        ],
        capture_output=True,
        timeout=120,
    )
    os.remove(local)
    return name if proc.returncode == 0 else ""


async def _generate_comfyui(
    model: dict,
    prompt: str,
    image_data: BytesIO | None = None,
) -> tuple[list[str] | None, str | None, dict | None]:
    """Generate (txt2img) or edit (img2img) via ComfyUI on the Vast box.

    Generates only the base image (no refine cascade). The remote paths are
    returned in meta["comfyui_remotes"] so the confirm flow can refine them in a
    second pass. Returns (lista de local_paths, err, meta)."""
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
            remotes, _rc = await _comfyui_run_remote(cmd, prompt)
        else:
            name = await _comfyui_upload(image_data)
            if not name:
                return None, "No pude subir la imagen al box de ComfyUI.", None
            cmd = (
                f"MODEL='{cm}' LORA='{cl}' INPUT_IMAGE='{name}' "
                f"python3 /workspace/gen_comfy.py"
            )
            remotes, _rc = await _comfyui_run_remote(cmd, prompt)
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


def _validate_refine_remote_path(p: str) -> bool:
    """Los paths vienen del stdout del box; validar charset para poder embeberlos
    en el shell con seguridad (sin comillas simples ni meta-char)."""
    return bool(p) and len(p) <= 300 and _REFINE_REMOTE_PATH_RE.fullmatch(p) is not None


async def _generate_comfyui_refine(
    model: dict,
    prompt: str,
    remote_paths: list[str],
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
        remotes, rc = await _comfyui_run_remote(cmd, prompt, timeout=refine_timeout)
        if rc == 2:
            return None, "Configuración de refino inválida en el box."
        if rc == 3:
            return None, "El refino no produjo imágenes."
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
    reply_markup and save_ref are configurable (used to attach the refine
    confirm keyboard to the base). Returns the sent message, or None if the file
    could not be read."""
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


def _comfyui_is_video(model: dict) -> bool:
    """Los modelos de video (Wan 2.2) devuelven MP4; el resto imágenes."""
    return model.get("comfyui_model") == "wan_i2v"


async def _send_comfyui_video(
    output: object,
    prompt: str,
    status_msg: types.Message,
    message: types.Message,
    prefix: str,
    regen_context: dict,
    model: dict | None = None,
    delete_status: bool = True,
) -> bool:
    """Send a ComfyUI Wan/MiniMax MP4 result to Telegram as a video."""
    try:
        with open(str(output), "rb") as f:
            video = BufferedInputFile(f.read(), filename="wan2.mp4")
    except (OSError, TypeError):
        await status_msg.edit_text("No se pudo leer el video generado.")
        return False
    sent_msg = await message.answer_video(
        video,
        caption=_format_result_caption(prefix, prompt, model=model),
        parse_mode="HTML",
        reply_markup=_image_regenerate_keyboard(),
    )
    sessions.save_generation_ref(
        message.chat.id,
        sent_msg.message_id,
        provider="comfyui",
        kind="video",
        prompt=prompt,
        regen=regen_context,
    )
    if delete_status:
        await status_msg.delete()
    return True


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
    A refinable generation (comfyui_refine==1 y meta trae comfyui_remotes)
    entra al flujo en 2 etapas con confirmación interactiva."""
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
    """Two-stage flow: send the base + confirm keyboard, wait for the decision
    (TTL = REFINE_CONFIRM_TIMEOUT), then refine or finalize the base.
    Álbum: the base is an album (no inline keyboard) so the confirm keyboard rides
    on a SEPARATE text message; the base album is kept as-is."""
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
        base_album = await _send_comfyui_album(
            output, prompt, status_msg, message, prefix, regen_context,
            model=model, delete_status=False, save_ref=True,
        )
        if base_album is None:
            # No base to confirm on — don't post a confirm prompt for images the
            # user never saw.
            _drop_pending_refine(token)
            return False
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
        # Refine started: drop the confirm keyboard and signal progress so the UI
        # doesn't sit on a dead "Refinar" button for the whole (long) refine.
        if is_album:
            if confirm_msg is not None:
                try:
                    await safe_edit_text(confirm_msg, "Refinando…", reply_markup=None)
                except Exception:
                    pass
        else:
            try:
                await base_msg.edit_reply_markup(reply_markup=_refining_keyboard())
            except Exception:
                pass
        if status_msg is not None:
            try:
                # Only show a Cancelar button when a real job backs this refine.
                # In no-job flows (reply/text-gen) cancel_event is None and a
                # jobless "cancel_job" button would cancel an UNRELATED in-flight
                # job (handle_cancel_job falls back to job_id=None → most recent).
                await status_msg.edit_text(
                    "Refinando…",
                    reply_markup=_cancel_job_keyboard(cancel_event)
                    if cancel_event is not None else None,
                )
            except Exception:
                pass

        refined, rerr = await _generate_comfyui_refine(
            model, prompt, list(meta.get("comfyui_remotes", [])),
        )

        if _job_cancelled(cancel_event):
            # Cancel during the refine step: do not deliver the refined image.
            # The base stays as the result; the "Cancelando…" status is left
            # untouched (the running loop owns it from here).
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
            refined_album = await _send_comfyui_album(
                refined, prompt, status_msg, message, prefix, regen_context,
                model=model, delete_status=delete_status,
            )
            if refined_album is None:
                # Refined send failed: clean up the dangling confirm message,
                # keep the base album, and report the failure.
                if confirm_msg is not None:
                    try:
                        await confirm_msg.delete()
                    except Exception:
                        pass
                await status_msg.edit_text(
                    "No se pudieron enviar las imágenes refinadas.", reply_markup=None
                )
                return False
            if confirm_msg is not None:
                try:
                    await confirm_msg.delete()
                except Exception:
                    pass
        else:
            single_refined = refined[0] if isinstance(refined, list) else refined
            ok = await _send_comfyui_image(
                single_refined, prompt, status_msg, message, prefix, regen_context,
                model=model, delete_status=delete_status,
            )
            if ok is None:
                # Refined send failed: surface the error, restore the base to
                # its final state, and report the failure truthfully.
                if status_msg is not None:
                    try:
                        await status_msg.edit_text(
                            "No se pudo enviar la imagen refinada.", reply_markup=None
                        )
                    except Exception:
                        pass
                try:
                    await base_msg.edit_reply_markup(
                        reply_markup=_image_regenerate_keyboard()
                    )
                except Exception:
                    pass
                return False
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


XAI_BASE = "https://api.x.ai/v1"


async def _generate_xai(
    model: dict,
    prompt: str,
    image_data: BytesIO | None = None,
    *,
    reference_image: BytesIO | None = None,
) -> tuple[object | None, str | None]:
    headers = {
        "Authorization": f"Bearer {XAI_API_KEY}",
        "Content-Type": "application/json",
    }

    if image_data and reference_image:
        for img in (image_data, reference_image):
            size_err = _validate_image_for_i2v(img)
            if size_err:
                return None, size_err
        body = {
            "model": model["id"],
            "prompt": prompt,
            "images": [
                {"url": _image_to_data_uri(image_data), "type": "image_url"},
                {"url": _image_to_data_uri(reference_image), "type": "image_url"},
            ],
        }
        url = f"{XAI_BASE}/images/edits"
    elif image_data:
        size_err = _validate_image_for_i2v(image_data)
        if size_err:
            return None, size_err
        body = {
            "model": model["id"],
            "prompt": prompt,
            "image": {"url": _image_to_data_uri(image_data), "type": "image_url"},
        }
        url = f"{XAI_BASE}/images/edits"
    else:
        body = {
            "model": model["id"],
            "prompt": prompt,
            "n": 1,
            "aspect_ratio": sessions.DEFAULT_IMAGE_ASPECT_RATIO,
        }
        url = f"{XAI_BASE}/images/generations"

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=body) as resp:
            if resp.status != 200:
                await resp.text()
                _log_xai_error(resp.status)
                return None, _xai_user_error("generación de imagen")
            data = await resp.json()

    urls = [r["url"] for r in data.get("data", []) if isinstance(r, dict) and r.get("url")]
    if urls:
        return urls, None
    return None, "xAI no devolvio URL de imagen"


KIE_BASE = "https://api.kie.ai"
KIE_UPLOAD_BASE = "https://kieai.redpandaai.co"
KIE_IMAGE_I2I = "grok-imagine/image-to-image"
KIE_VIDEO_T2V = "grok-imagine/text-to-video"
KIE_VIDEO_I2V = "grok-imagine/image-to-video"
# grok-imagine-video-1.5 on Kie.ai is i2v-only (slug verified via API probe).
KIE_VIDEO_15_I2V = "grok-imagine-video-1-5-preview"

_KIE_STATUS_LABELS = {
    "waiting": "en cola",
    "queuing": "en cola",
    "generating": "procesando",
}


def _kie_video_slug(video_model: str, *, image_to_video: bool) -> str:
    """Map bot video model selection to Kie.ai model slug."""
    if video_model == "grok-imagine-video-1.5":
        if image_to_video:
            return KIE_VIDEO_15_I2V
        # Kie 1.5 has no t2v slug; fall back to base text-to-video.
        return KIE_VIDEO_T2V
    return KIE_VIDEO_I2V if image_to_video else KIE_VIDEO_T2V


def _kie_headers() -> dict:
    return {
        "Authorization": f"Bearer {KIE_API_KEY}",
        "Content-Type": "application/json",
    }


def _log_kie_error(status: int, task_id: str | None = None) -> None:
    suffix = f" task_id={task_id}" if task_id else ""
    print(f"[kie error] status={status}{suffix}")


async def _kie_upload_image(session: aiohttp.ClientSession, image_data: BytesIO) -> tuple[str | None, str | None]:
    """Upload image to kie.ai and return a public URL for image_urls fields."""
    mime, ext = _detect_image_mime(image_data)
    data_uri = _image_to_data_uri(image_data, mime=mime)
    body = {
        "base64Data": data_uri,
        "uploadPath": "grok-bot",
        "fileName": f"upload-{int(time.time())}.{ext}",
    }
    async with session.post(
        f"{KIE_UPLOAD_BASE}/api/file-base64-upload",
        headers=_kie_headers(),
        json=body,
    ) as resp:
        if resp.status != 200:
            await resp.text()
            _log_kie_error(resp.status)
            return None, _kie_user_error("subida de imagen")
        data = await resp.json()
    if data.get("success") is False or data.get("code") not in (None, 200):
        _log_kie_error(data.get("code", 0))
        return None, _kie_user_error("subida de imagen")
    payload = data.get("data") or {}
    file_url = payload.get("fileUrl") or payload.get("downloadUrl")
    if not file_url:
        return None, "No se pudo subir la imagen. Intenta de nuevo."
    if not _is_allowed_kie_asset_url(file_url):
        print(f"[kie upload] blocked fileUrl host: {urllib.parse.urlparse(file_url).hostname}")
        return None, _kie_user_error("subida de imagen")
    return file_url, None


async def _kie_create_task(
    session: aiohttp.ClientSession,
    model_slug: str,
    input_data: dict,
) -> tuple[str | None, str | None]:
    body = {"model": model_slug, "input": input_data}
    async with session.post(
        f"{KIE_BASE}/api/v1/jobs/createTask",
        headers=_kie_headers(),
        json=body,
    ) as resp:
        if resp.status != 200:
            await resp.text()
            _log_kie_error(resp.status)
            return None, _kie_user_error("inicio de tarea")
        data = await resp.json()
    if data.get("code") != 200:
        _log_kie_error(data.get("code", 0))
        return None, _kie_user_error("inicio de tarea")
    task_id = (data.get("data") or {}).get("taskId")
    if not task_id:
        return None, "No se pudo iniciar la generación. Intenta de nuevo."
    return task_id, None


async def _kie_poll_task(
    session: aiohttp.ClientSession,
    task_id: str,
    *,
    status_msg: types.Message | None = None,
    status_label: str = "",
    prompt: str = "",
    max_poll_sec: int = VIDEO_MAX_POLL_SEC,
) -> tuple[list[str] | None, str | None]:
    """Poll kie.ai task until success/fail or timeout. Returns all result URLs."""
    started = time.monotonic()
    last_status = None
    last_elapsed_shown = -1
    last_state_printed: str | None = None
    label = ""

    while time.monotonic() - started < max_poll_sec:
        poll_data, poll_err, transient = await _kie_poll_once(session, task_id)
        if poll_err:
            if transient:
                print(f"[kie poll] transient error, retrying task_id={task_id}")
                await asyncio.sleep(VIDEO_POLL_INTERVAL_SEC)
                continue
            return None, poll_err

        state = (poll_data.get("data") or {}).get("state", "unknown")
        elapsed = int(time.monotonic() - started)

        if state == "success":
            print(f"[kie poll] state=success task_id={task_id} elapsed={elapsed}s")
            result_json_raw = (poll_data.get("data") or {}).get("resultJson")
            if not result_json_raw:
                return None, "No se recibió resultado. Intenta de nuevo."
            try:
                result_json = json.loads(result_json_raw) if isinstance(result_json_raw, str) else result_json_raw
            except (json.JSONDecodeError, TypeError):
                return None, "No se pudo interpretar el resultado. Intenta de nuevo."
            urls = result_json.get("resultUrls") or []
            if not urls:
                return None, "No se recibió URL de resultado. Intenta de nuevo."
            allowed = []
            for u in urls:
                if _is_allowed_kie_asset_url(u):
                    allowed.append(u)
                else:
                    print(f"[kie poll] blocked result host: {urllib.parse.urlparse(u).hostname}")
            if not allowed:
                return None, _kie_user_error("descarga de resultado")
            return allowed, None

        if state == "fail":
            fail_data = poll_data.get("data") or {}
            fail_code = fail_data.get("failCode")
            fail_msg = _sanitize_kie_fail_log(fail_data.get("failMsg"))
            print(
                f"[kie poll] state=fail task_id={task_id} elapsed={elapsed}s "
                f"failCode={fail_code} failMsg={fail_msg}"
            )
            return None, _kie_user_error("generación")

        if state != last_state_printed:
            label = _KIE_STATUS_LABELS.get(state, "")
            print(f"[kie poll] state={state} task_id={task_id} elapsed={elapsed}s")
            last_state_printed = state

        if status_msg and status_label:
            if state in _KIE_STATUS_LABELS and state != last_status:
                label = _KIE_STATUS_LABELS[state]
                await safe_edit_text(
                    status_msg,
                    _video_status_message(status_label, label, prompt),
                    parse_mode="HTML",
                )
                last_status = state
                last_elapsed_shown = elapsed
            elif state not in _KIE_STATUS_LABELS:
                if elapsed - last_elapsed_shown >= 30:
                    await safe_edit_text(
                        status_msg,
                        _video_status_message(status_label, f"({elapsed}s transcurridos)", prompt),
                        parse_mode="HTML",
                    )
                    last_elapsed_shown = elapsed

        await asyncio.sleep(VIDEO_POLL_INTERVAL_SEC)

    return None, "Tiempo de espera agotado (10 min). Intenta de nuevo."


def _kie_poll_error_is_transient(http_status: int, api_code: int | None = None) -> bool:
    if http_status in (404, 422, 429):
        return True
    if api_code in (422, 429):
        return True
    return http_status >= 500


async def _kie_poll_once(
    session: aiohttp.ClientSession,
    task_id: str,
) -> tuple[dict | None, str | None, bool]:
    """Poll once. Third value is True when the outer loop should retry until timeout."""
    url = f"{KIE_BASE}/api/v1/jobs/recordInfo?taskId={urllib.parse.quote(task_id)}"
    for attempt in range(POLL_MAX_RETRIES + 1):
        try:
            async with session.get(url, headers=_kie_headers()) as poll_resp:
                if poll_resp.status == 429:
                    if attempt < POLL_MAX_RETRIES:
                        await asyncio.sleep(POLL_RETRY_BACKOFF_SEC[attempt])
                        continue
                    return None, _kie_user_error("consulta de tarea"), True
                if poll_resp.status >= 500:
                    if attempt < POLL_MAX_RETRIES:
                        await asyncio.sleep(POLL_RETRY_BACKOFF_SEC[attempt])
                        continue
                    await poll_resp.text()
                    _log_kie_error(poll_resp.status, task_id)
                    return None, _kie_user_error("consulta de tarea"), True
                if poll_resp.status in (404, 422):
                    await poll_resp.text()
                    _log_kie_error(poll_resp.status, task_id)
                    return None, _kie_user_error("consulta de tarea"), True
                if poll_resp.status != 200:
                    await poll_resp.text()
                    _log_kie_error(poll_resp.status, task_id)
                    return None, _kie_user_error("consulta de tarea"), False
                data = await poll_resp.json()
                api_code = data.get("code")
                if api_code != 200:
                    _log_kie_error(api_code or 0, task_id)
                    transient = _kie_poll_error_is_transient(200, api_code)
                    return None, _kie_user_error("consulta de tarea"), transient
                return data, None, False
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            print(f"[kie poll] transient error: {exc}")
            if attempt < POLL_MAX_RETRIES:
                await asyncio.sleep(POLL_RETRY_BACKOFF_SEC[attempt])
                continue
            return None, _kie_user_error("consulta de tarea"), True
    return None, _kie_user_error("consulta de tarea"), True


async def _kie_get_result_url_at_index(
    session: aiohttp.ClientSession,
    task_id: str,
    index: int = 0,
) -> tuple[str | None, str | None]:
    """Fetch a result URL from a completed Kie task (for i2i from bot-generated images)."""
    poll_data, poll_err, _ = await _kie_poll_once(session, task_id)
    if poll_err:
        return None, poll_err
    state = (poll_data.get("data") or {}).get("state")
    if state != "success":
        return None, "La imagen de referencia no está disponible. Intenta de nuevo."
    result_json_raw = (poll_data.get("data") or {}).get("resultJson")
    if not result_json_raw:
        return None, "No se recibió resultado de la imagen de referencia."
    try:
        result_json = json.loads(result_json_raw) if isinstance(result_json_raw, str) else result_json_raw
    except (json.JSONDecodeError, TypeError):
        return None, "No se pudo interpretar la imagen de referencia."
    urls = result_json.get("resultUrls") or []
    if not urls:
        return None, "No se encontró URL de la imagen de referencia."
    idx = max(0, min(int(index), len(urls) - 1))
    result_url = urls[idx]
    if not _is_allowed_kie_asset_url(result_url):
        print(f"[kie ref] blocked result host: {urllib.parse.urlparse(result_url).hostname}")
        return None, _kie_user_error("descarga de resultado")
    return result_url, None


def _retry_status_text(status_label: str, attempt: int) -> str:
    """Append the current attempt number to a status message during retries."""
    return f"{status_label} (intento {attempt + 2}/{GENERATE_MAX_RETRIES + 1})"


async def _update_retry_status(
    status_msg: types.Message | None,
    status_label: str,
    status_parse_mode: str | None,
    attempt: int,
) -> None:
    if status_msg is None or not status_label:
        return
    # Telegram drops the inline keyboard when editMessageText omits reply_markup,
    # so the Cancel button would vanish on the first retry. Re-apply the current
    # one (the local Message object keeps its original keyboard across edits).
    await safe_edit_text(
        status_msg,
        _retry_status_text(status_label, attempt),
        parse_mode=status_parse_mode,
        reply_markup=status_msg.reply_markup,
    )


async def _generate_kie_once(
    model: dict,
    prompt: str,
    image_data: BytesIO | None = None,
    *,
    kie_source_ref: dict | None = None,
) -> tuple[object | None, str | None, dict | None]:
    """Single Kie generation attempt (no retry loop — the loop lives in generate_image).

    Precondition failures (missing API key, unresolved reference, oversized image)
    return meta={"retryable": False} so the caller short-circuits; generation
    failures return meta=None (retryable).
    """
    if not KIE_API_KEY:
        return None, _KIE_NOT_CONFIGURED_MSG, {"retryable": False}

    source_image_url: str | None = None
    if kie_source_ref:
        source_index = kie_source_ref.get("index", 0)
        async with aiohttp.ClientSession(timeout=KIE_REQUEST_TIMEOUT) as session:
            source_image_url, ref_err = await _kie_get_result_url_at_index(
                session,
                kie_source_ref["task_id"],
                source_index,
            )
            if ref_err:
                return None, ref_err, {"retryable": False}

    if image_data and not kie_source_ref:
        size_err = _validate_image_for_i2v(image_data)
        if size_err:
            return None, size_err, {"retryable": False}

    is_image_task = bool(kie_source_ref or image_data)
    poll_timeout = IMAGE_MAX_POLL_SEC if is_image_task else VIDEO_MAX_POLL_SEC

    async with aiohttp.ClientSession(timeout=KIE_REQUEST_TIMEOUT) as session:
        if kie_source_ref:
            input_data: dict = {
                "image_urls": [source_image_url],
                "prompt": prompt,
                "enable_pro": True,
                "nsfw_checker": False,
                "mode": "spicy",
            }
            model_slug = KIE_IMAGE_I2I
        elif image_data:
            image_data.seek(0)
            image_url, upload_err = await _kie_upload_image(session, image_data)
            if upload_err:
                return None, upload_err, None
            input_data = {
                "image_urls": [image_url],
                "prompt": prompt,
                "enable_pro": True,
                "nsfw_checker": False,
                "mode": "normal",
            }
            model_slug = KIE_IMAGE_I2I
        else:
            input_data = {
                "prompt": prompt,
                "aspect_ratio": sessions.DEFAULT_IMAGE_ASPECT_RATIO,
                "enable_pro": True,
                "nsfw_checker": False,
            }
            model_slug = model["id"]

        task_id, create_err = await _kie_create_task(session, model_slug, input_data)
        if create_err:
            return None, create_err, None

        result_urls, poll_err = await _kie_poll_task(session, task_id, max_poll_sec=poll_timeout)
        if poll_err:
            return None, poll_err, None

        kie_meta = {"task_id": task_id, "index": 0, "provider": "kie"}
        return result_urls, None, kie_meta


def _normalize_image_urls(output) -> list[str]:
    raw = output if isinstance(output, list) else [output]
    urls = []
    for item in raw:
        if hasattr(item, "url"):
            item = item.url
        if item is not None:
            urls.append(str(item))
    return urls


async def process_image_result(
    output,
    prompt: str,
    status_msg: types.Message,
    message: types.Message,
    prefix: str,
    *,
    download_allowlist: str | None = None,
    kie_meta: dict | None = None,
    regen_context: dict | None = None,
    delete_status: bool = True,
    model: dict | None = None,
):
    if output is None:
        await status_msg.edit_text("Error: el modelo no devolvio nada. Intenta con otro prompt.")
        return

    urls = _normalize_image_urls(output)
    if not urls:
        await status_msg.edit_text("Error: el modelo no devolvio ninguna URL. Intenta con otro prompt.")
        return

    provider = (
        kie_meta.get("provider")
        if kie_meta and kie_meta.get("task_id")
        else (regen_context or {}).get("provider", "unknown")
    )

    if len(urls) == 1:
        image_bytes, dl_err = await download_url(urls[0], download_allowlist=download_allowlist)
        if dl_err:
            await status_msg.edit_text(dl_err)
            return
        photo = BufferedInputFile(image_bytes, filename="generated.png")
        sent_msg = await message.answer_photo(
            photo,
            caption=_format_result_caption(prefix, prompt, model=model),
            parse_mode="HTML",
            reply_markup=_image_regenerate_keyboard(),
            reply_to_message_id=message.message_id,
            allow_sending_without_reply=True,
        )
        sessions.save_generation_ref(
            message.chat.id,
            sent_msg.message_id,
            kie_task_id=kie_meta.get("task_id") if kie_meta else None,
            kie_index=kie_meta.get("index", 0) if kie_meta else 0,
            provider=provider,
            kind="image",
            prompt=prompt,
            regen=regen_context,
        )
        if delete_status:
            await status_msg.delete()
        return

    total = len(urls)
    for i, url in enumerate(urls):
        image_bytes, dl_err = await download_url(url, download_allowlist=download_allowlist)
        if dl_err:
            await status_msg.edit_text(dl_err)
            return
        photo = BufferedInputFile(image_bytes, filename="generated.png")
        sent_msg = await message.answer_photo(
            photo,
            caption=_format_result_caption(prefix, prompt, variant=f"{i + 1}/{total}", model=model),
            parse_mode="HTML",
            reply_markup=_image_regenerate_keyboard(),
            reply_to_message_id=message.message_id,
            allow_sending_without_reply=True,
        )
        sessions.save_generation_ref(
            message.chat.id,
            sent_msg.message_id,
            kie_task_id=kie_meta.get("task_id") if kie_meta else None,
            kie_index=i,
            provider=provider,
            kind="image",
            prompt=prompt,
            regen=regen_context,
        )
    if delete_status:
        await status_msg.delete()


async def download_url(
    url: str,
    *,
    max_bytes: int = DOWNLOAD_MAX_BYTES,
    enforce_host_allowlist: bool = False,
    download_allowlist: str | None = None,
) -> tuple[bytes | None, str | None]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        return None, "No se pudo descargar el archivo (URL no permitida)."

    allowlist = download_allowlist
    if allowlist is None and enforce_host_allowlist:
        allowlist = "xai"

    host = (parsed.hostname or "").lower()
    if allowlist and not _is_host_allowed_for_download(host, allowlist):
        print(f"[download_url] blocked host: {host}")
        return None, "No se pudo descargar el archivo (origen no permitido)."

    timeout = aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT_SEC)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, allow_redirects=True) as resp:
                if resp.status != 200:
                    return None, "No se pudo descargar el archivo. Intenta de nuevo."
                if allowlist:
                    final_host = (urllib.parse.urlparse(str(resp.url)).hostname or "").lower()
                    if not _is_host_allowed_for_download(final_host, allowlist):
                        print(f"[download_url] blocked redirect host: {final_host}")
                        return None, "No se pudo descargar el archivo (origen no permitido)."
                chunks: list[bytes] = []
                total = 0
                async for chunk in resp.content.iter_chunked(65536):
                    total += len(chunk)
                    if total > max_bytes:
                        return None, "El archivo es demasiado grande para descargar."
                    chunks.append(chunk)
                data = b"".join(chunks)
                if not data:
                    return None, "El archivo descargado está vacío."
                return data, None
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        print(f"[download_url] error: {exc}")
        return None, "No se pudo descargar el archivo. Intenta de nuevo."


# ---------------------------------------------------------------------------
# Video generation (grok_video via xAI)
# ---------------------------------------------------------------------------
_VIDEO_STATUS_LABELS = {
    "pending": "en cola",
    "processing": "procesando",
}


async def generate_video(
    model: dict,
    prompt: str,
    image_data: BytesIO | None = None,
    *,
    kie_source_ref: dict | None = None,
    status_msg: types.Message | None = None,
    user_id: int | None = None,
) -> tuple[str | None, str | None]:
    prov = model.get("provider", "?")
    if user_id is not None:
        model_id = sessions.get_video_config(user_id)["model"]
    else:
        model_id = sessions.DEFAULT_VIDEO_MODEL
    print(
        f"[generate_video] key={model.get('key')} provider={prov} id={model_id} "
        f"has_image={image_data is not None} kie_ref={kie_source_ref is not None}"
    )
    if prov == "xai":
        return await _generate_xai_video(
            model,
            prompt,
            image_data,
            status_msg=status_msg,
            user_id=user_id,
        )
    if prov == "kie":
        return await _generate_kie_video(
            model,
            prompt,
            image_data,
            kie_source_ref=kie_source_ref,
            status_msg=status_msg,
            user_id=user_id,
        )
    return None, "Proveedor no soportado para generación de video."


async def _generate_xai_video(
    model: dict,
    prompt: str,
    image_data: BytesIO | None = None,
    *,
    status_msg: types.Message | None = None,
    user_id: int | None = None,
) -> tuple[str | None, str | None]:
    headers = {
        "Authorization": f"Bearer {XAI_API_KEY}",
        "Content-Type": "application/json",
    }

    video_cfg = sessions.get_video_config(user_id) if user_id is not None else {
        "duration": sessions.DEFAULT_VIDEO_DURATION,
        "aspect_ratio": sessions.DEFAULT_VIDEO_ASPECT_RATIO,
        "resolution": sessions.DEFAULT_VIDEO_RESOLUTION,
        "model": sessions.DEFAULT_VIDEO_MODEL,
    }

    model_id = video_cfg["model"]
    body: dict = {
        "model": model_id,
        "prompt": prompt,
        "duration": video_cfg["duration"],
        "aspect_ratio": video_cfg["aspect_ratio"],
        "resolution": video_cfg["resolution"],
    }

    if image_data:
        size_err = _validate_image_for_i2v(image_data)
        if size_err:
            return None, size_err
        body["image"] = {"url": _image_to_data_uri(image_data)}

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{XAI_BASE}/videos/generations",
            headers=headers,
            json=body,
        ) as resp:
            if not _xai_http_ok(resp.status):
                await resp.text()
                _log_xai_error(resp.status)
                return None, _xai_user_error("generación de video")
            data = await resp.json()

        request_id = data.get("request_id")
        if not request_id:
            return None, "No se pudo iniciar la generación de video. Intenta de nuevo."

        started = time.monotonic()
        last_status = None
        last_elapsed_shown = -1

        while time.monotonic() - started < VIDEO_MAX_POLL_SEC:
            poll_data, poll_err = await _poll_video_once(session, request_id, headers)
            if poll_err:
                return None, poll_err

            status = poll_data.get("status", "unknown")

            if status == "done":
                video = poll_data.get("video") or {}
                respect_moderation = video.get(
                    "respect_moderation",
                    poll_data.get("respect_moderation"),
                )
                if respect_moderation is False:
                    return None, "El contenido no cumple las políticas de moderación."
                video_url = video.get("url")
                if not video_url:
                    return None, "No se recibió URL de video. Intenta de nuevo."
                return video_url, None

            if status in ("failed", "expired"):
                print(f"[video poll] status={status} request_id={request_id}")
                return None, _xai_user_error(f"generación de video ({status})")

            if status_msg:
                elapsed = int(time.monotonic() - started)
                if status in _VIDEO_STATUS_LABELS and status != last_status:
                    label = _VIDEO_STATUS_LABELS[status]
                    await safe_edit_text(
                        status_msg,
                        _video_status_message(model_id, label, prompt),
                        parse_mode="HTML",
                    )
                    last_status = status
                    last_elapsed_shown = elapsed
                elif status not in _VIDEO_STATUS_LABELS:
                    print(f"[video poll] unknown status: {status} request_id={request_id}")
                    if elapsed - last_elapsed_shown >= 30:
                        await safe_edit_text(
                            status_msg,
                            _video_status_message(model_id, f"({elapsed}s transcurridos)", prompt),
                            parse_mode="HTML",
                        )
                        last_elapsed_shown = elapsed

            await asyncio.sleep(VIDEO_POLL_INTERVAL_SEC)

    return None, "Tiempo de espera agotado (10 min). Intenta de nuevo."


async def _poll_video_once(
    session: aiohttp.ClientSession,
    request_id: str,
    headers: dict,
) -> tuple[dict | None, str | None]:
    """Poll video status once with retries on transient errors."""
    url = f"{XAI_BASE}/videos/{request_id}"
    for attempt in range(POLL_MAX_RETRIES + 1):
        try:
            async with session.get(url, headers=headers) as poll_resp:
                if poll_resp.status >= 500:
                    if attempt < POLL_MAX_RETRIES:
                        await asyncio.sleep(POLL_RETRY_BACKOFF_SEC[attempt])
                        continue
                    await poll_resp.text()
                    _log_xai_error(poll_resp.status, request_id)
                    return None, _xai_user_error("consulta de video")
                if not _xai_http_ok(poll_resp.status):
                    await poll_resp.text()
                    _log_xai_error(poll_resp.status, request_id)
                    return None, _xai_user_error("consulta de video")
                return await poll_resp.json(), None
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            print(f"[video poll] transient error: {exc}")
            if attempt < POLL_MAX_RETRIES:
                await asyncio.sleep(POLL_RETRY_BACKOFF_SEC[attempt])
                continue
            return None, _xai_user_error("consulta de video")
    return None, _xai_user_error("consulta de video")


async def _generate_kie_video(
    model: dict,
    prompt: str,
    image_data: BytesIO | None = None,
    *,
    kie_source_ref: dict | None = None,
    status_msg: types.Message | None = None,
    user_id: int | None = None,
) -> tuple[str | None, str | None]:
    if not KIE_API_KEY:
        return None, _KIE_NOT_CONFIGURED_MSG

    video_cfg = sessions.get_video_config(user_id) if user_id is not None else {
        "duration": sessions.DEFAULT_VIDEO_DURATION,
        "aspect_ratio": sessions.DEFAULT_VIDEO_ASPECT_RATIO,
        "resolution": sessions.DEFAULT_VIDEO_RESOLUTION,
        "model": sessions.DEFAULT_VIDEO_MODEL,
        "mode": sessions.DEFAULT_VIDEO_MODE,
    }

    allowed_aspects = _kie_aspect_ratios_for_model(video_cfg["model"])
    if video_cfg["aspect_ratio"] not in allowed_aspects:
        supported = ", ".join(allowed_aspects)
        return None, f"Relación de aspecto no compatible con Kie.ai. Usa: {supported}"

    has_image = image_data is not None or kie_source_ref is not None
    status_label = _kie_video_status_label(video_cfg["model"], image_to_video=has_image)
    kie_duration = _kie_map_duration(video_cfg["duration"])
    configured_mode = video_cfg.get("mode", sessions.DEFAULT_VIDEO_MODE)

    input_data: dict = {
        "prompt": prompt,
        "aspect_ratio": video_cfg["aspect_ratio"],
        "duration": kie_duration,
        "resolution": video_cfg["resolution"],
        "nsfw_checker": False,
    }

    async with aiohttp.ClientSession(timeout=KIE_REQUEST_TIMEOUT) as session:
        if kie_source_ref:
            input_data["task_id"] = kie_source_ref["task_id"]
            input_data["index"] = kie_source_ref.get("index", 0)
        elif has_image:
            size_err = _validate_image_for_i2v(image_data)
            if size_err:
                return None, size_err
            image_url, upload_err = await _kie_upload_image(session, image_data)
            if upload_err:
                return None, upload_err
            input_data["image_urls"] = [image_url]

        model_slug = _kie_video_slug(video_cfg["model"], image_to_video=has_image)
        if model_slug != KIE_VIDEO_15_I2V:
            if kie_source_ref:
                input_data["mode"] = configured_mode
            else:
                # Spicy only works with Kie-generated images (task_id path).
                input_data["mode"] = "normal" if configured_mode == "spicy" else configured_mode

        task_id, create_err = await _kie_create_task(session, model_slug, input_data)
        if create_err:
            return None, create_err

        urls, err = await _kie_poll_task(
            session,
            task_id,
            status_msg=status_msg,
            status_label=status_label,
            prompt=prompt,
        )
        return (urls[0] if urls else None), err


async def process_video_result(
    video_url: str,
    prompt: str,
    status_msg: types.Message,
    message: types.Message,
    prefix: str,
    *,
    enforce_host_allowlist: bool = True,
    download_allowlist: str | None = None,
):
    if not video_url:
        await status_msg.edit_text("Error: el modelo no devolvió URL de video. Intenta con otro prompt.")
        return

    allowlist = download_allowlist
    if allowlist is None and enforce_host_allowlist:
        allowlist = "xai"

    video_bytes, dl_err = await download_url(
        str(video_url),
        max_bytes=DOWNLOAD_MAX_BYTES,
        download_allowlist=allowlist,
    )
    if dl_err:
        await status_msg.edit_text(dl_err)
        return

    if len(video_bytes) > TELEGRAM_MAX_VIDEO_BYTES:
        await status_msg.edit_text(
            f"El video es demasiado grande para Telegram ({len(video_bytes) // 1024 // 1024} MB).\n"
            f"Descárgalo aquí:\n{video_url}{_SENSITIVE_DOWNLOAD_WARNING}"
        )
        return

    video = BufferedInputFile(video_bytes, filename="generated.mp4")
    try:
        await message.answer_video(
            video,
            caption=_format_result_caption(prefix, prompt),
            parse_mode="HTML",
        )
        await status_msg.delete()
    except TelegramBadRequest as exc:
        print(f"[video] answer_video failed: {exc}")
        await status_msg.edit_text(
            "No se pudo enviar el video por Telegram.\n"
            f"Descárgalo aquí:\n{video_url}{_SENSITIVE_DOWNLOAD_WARNING}"
        )


# ---------------------------------------------------------------------------
# Unified /config FSM flow
# ---------------------------------------------------------------------------
import config_flow

_CONFIG_DEPS = {
        "MODELS": MODELS,
        "get_user_state": get_user_state,
        "clear_pending_faceswap": _clear_pending_faceswap,
        "get_grok_imagine_config": get_grok_imagine_config,
        "set_model": sessions.set_model,
        "set_grok_imagine_config": sessions.set_grok_imagine_config,
        "get_video_config": sessions.get_video_config,
        "set_video_config": sessions.set_video_config,
        "safe_edit_text": safe_edit_text,
        "GROK_IMAGINE_VARIANTS": GROK_IMAGINE_VARIANTS,
        "get_video_provider_for_user": get_video_provider_for_user,
        "get_comfyui_config": sessions.get_comfyui_config,
        "set_comfyui_config": sessions.set_comfyui_config,
        "VALID_COMFYUI_MODELS": sessions.VALID_COMFYUI_MODELS,
        "VALID_COMFYUI_LORAS": sessions.VALID_COMFYUI_LORAS,
        "_maybe_reset_kie_aspect_ratio": _maybe_reset_kie_aspect_ratio,
        "_kie_aspect_ratios_for_model": _kie_aspect_ratios_for_model,
        "_video_config_summary": _video_config_summary,
        "_video_duration_display": _video_duration_display,
        "VIDEO_MODEL_LABELS": VIDEO_MODEL_LABELS,
        "VIDEO_MODE_LABELS": VIDEO_MODE_LABELS,
        "_prov_label": _prov_label,
        "kie_configured": bool(KIE_API_KEY),
        "_KIE_NOT_CONFIGURED_MSG": _KIE_NOT_CONFIGURED_MSG,
        "_KIE_QUALITY_NOTE": _KIE_QUALITY_NOTE,
        "_KIE_PRIVACY_NOTICE": _KIE_PRIVACY_NOTICE,
}

config_flow.register_config_handlers(dp, _CONFIG_DEPS)

# Re-exports for tests (unified /config flow)
cmd_config = config_flow.cmd_config
cmd_model = config_flow.cmd_model
cmd_imaginess = config_flow.cmd_imaginess
cmd_video = config_flow.cmd_video
handle_cfg_model = config_flow.handle_cfg_model
handle_cfg_provider = config_flow.handle_cfg_provider
handle_cfg_variant = config_flow.handle_cfg_variant
handle_cfg_video = config_flow.handle_cfg_video
handle_cfg_back_model = config_flow.handle_cfg_back_model
handle_cfg_back_provider = config_flow.handle_cfg_back_provider
handle_cfg_close = config_flow.handle_cfg_close
def config_model_keyboard(user_id: int):
    return config_flow.config_model_keyboard(_CONFIG_DEPS, user_id)


def config_provider_keyboard(user_id: int):
    return config_flow.config_provider_keyboard(_CONFIG_DEPS, user_id)


def config_variant_keyboard(user_id: int):
    return config_flow.config_variant_keyboard(_CONFIG_DEPS, user_id)


def config_video_keyboard(current: dict, user_id: int):
    return config_flow.config_video_keyboard(_CONFIG_DEPS, current, user_id)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
