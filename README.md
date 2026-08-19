# grok

Telegram bot for image and video generation via xAI Grok Imagine and Replicate.

## Models

- **Grok Imagine** — image generation/editing (xAI, Replicate, or Kie.ai)
- **Grok Imagine Video** — text-to-video and image-to-video (xAI or Kie.ai)
- **Seedream 5.0** — image generation (Replicate)
- **Face Swap** — face swap (Replicate)

## Video generation

Use `/config` to select models and adjust settings. Legacy aliases: `/model`, `/imagine`, `/imaginess`, `/video`.

CRÍTICO: No leer logs de prompts/registros de generación

Select **Grok Imagine Video** via `/config` (or `/model`), then:

- Send a text prompt to generate a video (with confirmation)
- Send a photo with caption to animate it (image-to-video)
- Reply to a photo with text to animate it

Defaults: 5s duration, 16:9 aspect ratio, 720p resolution (persisted per user in `sessions.json`).

Use `/config` (or `/video`) to change the model (`grok-imagine-video` base or `grok-imagine-video-1.5` recent), duration (3/5/10/15s), aspect ratio, and resolution (480p/720p).

**Kie.ai video constraints** (when Kie.ai is selected via `/config` or `/imaginess`): duration is clamped to 6–30s (3/5s become 6s); base model supports aspect ratios 16:9, 9:16, 1:1, 3:2, 2:3; model 1.5 adds 4:3 and 3:4 and is image-to-video only on Kie.ai. Replicate image provider still uses xAI for video.

**Data privacy:** When Kie.ai is the active provider, prompts and uploaded images are sent to Kie.ai (third-party) for processing. Operators should inform users accordingly.

## Batch image editing with variables (/variables)

`/variables` runs a batch of image edits on the user's **configured image model/provider** (Grok Imagine via xAI, Replicate, or Kie.ai; Seedream; ComfyUI image). Video models (Grok Imagine Video, ComfyUI Wan i2v) and Face Swap are rejected — pick an image model in `/config`. Each edit uses a prompt built from a random combination of three admin-managed lists: **poses**, **ángulos** and **acciones**.

- Send a photo with caption `/variables 5` (or reply to a photo with `/variables 5`) to generate 5 edits. `N` is clamped to 1–10.
- Send `/variables 5` as a **plain text message** (no photo, no reply) to generate 5 images directly from the random list combos (text-to-image).
- Every iteration reuses the **original image** (results are never chained) and picks a fresh random pose/angle/action combo (repeating combos within a batch are avoided when possible).
- Generations run sequentially and relaunch automatically after each result; the batch is cancellable with the inline Cancel button. A failed item is skipped and the rest of the batch still runs. Up to 3 jobs can run at the same time for one user; starting a fourth is rejected until a slot frees.
- The `N` in the caption controls how many images to generate. Example: `/variables 3` → 3 independent edits, each with a different random combo.

The lists and the prompt template are managed with `/listas` (Telegram admin panel, private chats only): add / edit / delete items per list and customize the template with the `{pose}`, `{angle}`, `{action}` placeholders. Lists persist in `variables_lists.json`.

## ComfyUI image editing (refine confirmation)

When the configured ComfyUI model has refine enabled (default), each generated image goes through a 2-stage flow: the **base** is sent first with `[✨ Refinar][⏭ Continuar]` buttons.

- `✨ Refinar` re-refines the SAME base (same model/prompt; uses the box's `REFINE_ONLY` mode) and posts the refined image.
- `⏭ Continuar` keeps the base as the final result (the base moves to the "Regenerar" keyboard).
- If no decision arrives within the TTL (default **300 s**, env `REFINE_CONFIRM_TIMEOUT`), the base is final.
- Cancelling during a refine (`Cancelar`) in `/variables` stops the batch cleanly with "Cancelado. Completadas X/N" and the base is kept. (The album-batch variant of this path is not reachable for ComfyUI — dead branch, see below.)

ComfyUI multi-photo albums are NOT routed: `handle_album` does not process ComfyUI media groups (defensive/dead branch of the bot). The single-image confirm keyboard rides on the image itself.

### ComfyUI refine — deploy note (operator)

The refine flow requires the `REFINE_ONLY` mode on the ComfyUI box: `gen_comfy.py` must be the updated version (repo `comfyui-vast-setup`). Deploy is MANUAL (not automatic): `cp gen_comfy.py /workspace/gen_comfy.py` on the box. Without that deploy, refine is not available and the base is kept.

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | Telegram bot token |
| `REPLICATE_API_TOKEN` | Yes | Replicate API token |
| `XAI_API_KEY` | Yes | xAI API key |
| `KIE_API_KEY` | No | Kie.ai API key (required when using Kie.ai provider via `/config` or `/imaginess`) |
| `ALLOWED_TELEGRAM_IDS` | Yes (recommended) | Comma-separated user IDs; only these users can use the bot (enforced on all messages and callbacks) |
| `VARIABLES_ADMIN_IDS` | No | Comma-separated user IDs allowed to edit the `/listas` panel (poses/ángulos/acciones). Defaults to `ALLOWED_TELEGRAM_IDS` when unset, and to everyone when neither is set |
| `REFINE_CONFIRM_TIMEOUT` | No | TTL in seconds for the ComfyUI refine confirmation (default 300); without a decision the base is final |

## Deployment

**Allowlist:** Set `ALLOWED_TELEGRAM_IDS` to your Telegram user ID(s) before going live. Without it, anyone who discovers the bot can use it and consume your API tokens. Example: `ALLOWED_TELEGRAM_IDS=123456789,987654321`.

**FSM storage:** The bot uses aiogram `MemoryStorage` for the `/config` flow state. FSM data is lost on restart and is not shared across multiple bot processes. For production with restarts or horizontal scaling, switch to Redis-backed storage.

## Tests

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt
pytest tests/
```

Or without activating the venv:

```bash
./venv/bin/pip install -r requirements-dev.txt
./venv/bin/python -m pytest tests/ -q
```
