"""Tests for the /var <texto> command: inline-prompt single image generation.

Unlike /variables (random combos drawn from the JSON lists), /var injects the
text written after the command into the configured template: comma-separated
fields fill the placeholders positionally (a single field lands on the first
placeholder). It shares the routing structure (photo caption / reply / plain
text) and the output paths.
"""

from __future__ import annotations

from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import bot
import variables_store

RESULT_URL = "https://kieai.redpandaai.co/static/result.png"


def _make_photo_message(*, caption=None, user_id=1001, chat_id=2001, message_id=1, file_id="p1"):
    msg = MagicMock()
    msg.from_user.id = user_id
    msg.chat.id = chat_id
    msg.message_id = message_id
    msg.caption = caption
    msg.media_group_id = None
    msg.reply_to_message = None
    msg.photo = [MagicMock(file_id=file_id)]
    msg.answer = AsyncMock()
    msg.answer_photo = AsyncMock()
    return msg


def _make_reply_message(*, text, photo_file_id="r1", user_id=1001, chat_id=2001, message_id=2):
    msg = MagicMock()
    msg.from_user.id = user_id
    msg.chat.id = chat_id
    msg.message_id = message_id
    msg.text = text
    msg.caption = None
    msg.media_group_id = None
    msg.answer = AsyncMock()
    msg.answer_photo = AsyncMock()
    reply = MagicMock()
    reply.photo = [MagicMock(file_id=photo_file_id)]
    msg.reply_to_message = reply
    return msg


def _make_text_message(*, text, user_id=1001, chat_id=2001, message_id=3):
    msg = MagicMock()
    msg.from_user.id = user_id
    msg.chat.id = chat_id
    msg.message_id = message_id
    msg.text = text
    msg.caption = None
    msg.media_group_id = None
    msg.photo = None
    msg.reply_to_message = None
    msg.answer = AsyncMock()
    msg.answer_photo = AsyncMock()
    return msg


def _make_status():
    status = MagicMock()
    status.edit_text = AsyncMock()
    status.delete = AsyncMock()
    return status


def _set_user_image_config(uid=1001, *, model="grok", provider=None):
    """Mutate hydrated user_state; do not replace the whole dict."""
    state = bot.get_user_state(uid)
    state["model"] = model
    if provider is not None:
        state["grok_imagine_provider"] = provider
    return state


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------
def test_parse_var_prompt():
    assert bot._parse_var_prompt("/var de pie, frontal") == "de pie, frontal"
    assert bot._parse_var_prompt("  /var  texto con  espacios  ") == "texto con  espacios"
    assert bot._parse_var_prompt("/var@MyBot hola mundo") == "hola mundo"
    assert bot._parse_var_prompt("/VAR MULTILINEA\nsegunda linea") == "MULTILINEA\nsegunda linea"
    assert bot._parse_var_prompt("/var") is None
    assert bot._parse_var_prompt("/var ") is None
    assert bot._parse_var_prompt("/variables 2") is None  # not a /var invocation
    assert bot._parse_var_prompt("/variable") is None
    assert bot._parse_var_prompt(None) is None


def test_is_var_command():
    assert bot._is_var_command("/var de pie")
    assert bot._is_var_command("  /var 5  ")
    assert bot._is_var_command("/VAR")
    assert bot._is_var_command("/var@MyBot texto")
    assert not bot._is_var_command("var de pie")  # no leading slash
    assert not bot._is_var_command("/variables 2")  # never hijacked
    assert not bot._is_var_command("/variable x")  # prefix only
    assert not bot._is_var_command("/variablesfoo 2")
    assert not bot._is_var_command("/s de pie")
    assert not bot._is_var_command(None)


def test_parse_var_count_and_text():
    # count + prompt
    assert bot._parse_var_count_and_text("/var 5 de pie, frontal") == (5, "de pie, frontal")
    assert bot._parse_var_count_and_text("/var 10 de pie") == (bot.VARIABLES_MAX, "de pie")
    assert bot._parse_var_count_and_text("/var@MyBot 2 de perfil") == (2, "de perfil")
    # no count → 1 image
    assert bot._parse_var_count_and_text("/var de pie, frontal") == (1, "de pie, frontal")
    assert bot._parse_var_count_and_text("/var de pie") == (1, "de pie")
    # out-of-range numbers are prompt text, not counts
    assert bot._parse_var_count_and_text("/var 15 personas") == (1, "15 personas")
    assert bot._parse_var_count_and_text("/var 0 personas") == (1, "0 personas")
    # a lone number is prompt text (too short → validation error later)
    assert bot._parse_var_count_and_text("/var 5") == (1, "5")
    # invalid
    assert bot._parse_var_count_and_text("/var") == (1, None)
    assert bot._parse_var_count_and_text(None) == (1, None)


# ---------------------------------------------------------------------------
# Routing (defensive checks inside the generic handlers)
# ---------------------------------------------------------------------------
async def test_photo_caption_routes_to_var(sessions_file, variables_file):
    msg = _make_photo_message(caption="/var de pie, frontal")
    with patch.object(bot, "cmd_var_photo", new_callable=AsyncMock) as mock_cmd:
        await bot.handle_photo_caption(msg)
    mock_cmd.assert_awaited_once_with(msg)


async def test_photo_caption_variables_not_hijacked(sessions_file, variables_file):
    """'/variables 2' caption must still reach the variables batch, never /var."""
    msg = _make_photo_message(caption="/variables 2")
    with patch.object(bot, "cmd_var_photo", new_callable=AsyncMock) as mock_var:
        with patch.object(bot, "cmd_variables_photo", new_callable=AsyncMock) as mock_variables:
            await bot.handle_photo_caption(msg)
    mock_var.assert_not_awaited()
    mock_variables.assert_awaited_once_with(msg)


async def test_photo_caption_regular_caption_not_routed(sessions_file, variables_file):
    msg = _make_photo_message(caption="cambia el fondo a playa")
    with patch.object(bot, "cmd_var_photo", new_callable=AsyncMock) as mock_cmd:
        with patch.object(bot, "_process_single_photo_edit", new_callable=AsyncMock) as mock_edit:
            await bot.handle_photo_caption(msg)
    mock_cmd.assert_not_awaited()
    mock_edit.assert_awaited_once()


async def test_reply_routes_to_var(sessions_file, variables_file):
    msg = _make_reply_message(text="/var de perfil")
    with patch.object(bot, "cmd_var_reply", new_callable=AsyncMock) as mock_cmd:
        await bot.handle_reply_edit(msg)
    mock_cmd.assert_awaited_once_with(msg)


async def test_reply_variables_not_hijacked(sessions_file, variables_file):
    msg = _make_reply_message(text="/variables 3")
    with patch.object(bot, "cmd_var_reply", new_callable=AsyncMock) as mock_var:
        with patch.object(bot, "cmd_variables_reply", new_callable=AsyncMock) as mock_variables:
            await bot.handle_reply_edit(msg)
    mock_var.assert_not_awaited()
    mock_variables.assert_awaited_once_with(msg)


# ---------------------------------------------------------------------------
# cmd_var_help delegation
# ---------------------------------------------------------------------------
async def test_var_help_delegates_reply(sessions_file, variables_file):
    msg = _make_reply_message(text="/var de perfil")
    with patch.object(bot, "cmd_var_reply", new_callable=AsyncMock) as mock_cmd:
        await bot.cmd_var_help(msg)
    mock_cmd.assert_awaited_once_with(msg)
    msg.answer.assert_not_awaited()


async def test_var_help_delegates_photo_caption(sessions_file, variables_file):
    msg = _make_photo_message(caption="/var de pie")
    with patch.object(bot, "cmd_var_photo", new_callable=AsyncMock) as mock_cmd:
        await bot.cmd_var_help(msg)
    mock_cmd.assert_awaited_once_with(msg)
    msg.answer.assert_not_awaited()


async def test_var_help_photo_in_album_shows_usage(sessions_file, variables_file):
    msg = _make_photo_message(caption="/var de pie")
    msg.media_group_id = "mg-1"
    with patch.object(bot, "cmd_var_photo", new_callable=AsyncMock) as mock_cmd:
        await bot.cmd_var_help(msg)
    mock_cmd.assert_not_awaited()
    assert "/var texto" in msg.answer.call_args.args[0]


async def test_var_help_bare_text_runs_text_generation(sessions_file, variables_file):
    msg = _make_text_message(text="/var de pie, frontal")
    with patch.object(bot, "_run_var_batch", new_callable=AsyncMock) as mock_run:
        await bot.cmd_var_help(msg)
    mock_run.assert_awaited_once_with(msg, 1, "de pie, frontal", None, None, mode="text")
    msg.answer.assert_not_awaited()


async def test_var_help_text_with_count_runs_text_batch(sessions_file, variables_file):
    msg = _make_text_message(text="/var 3 de pie, frontal")
    with patch.object(bot, "_run_var_batch", new_callable=AsyncMock) as mock_run:
        await bot.cmd_var_help(msg)
    mock_run.assert_awaited_once_with(msg, 3, "de pie, frontal", None, None, mode="text")
    msg.answer.assert_not_awaited()


async def test_var_help_bare_command_shows_usage(sessions_file, variables_file):
    msg = _make_text_message(text="/var")
    with patch.object(bot, "_run_var_batch", new_callable=AsyncMock) as mock_run:
        await bot.cmd_var_help(msg)
    assert "/var texto" in msg.answer.call_args.args[0]
    mock_run.assert_not_awaited()


async def test_var_help_too_short_prompt_shows_error(sessions_file, variables_file):
    msg = _make_text_message(text="/var x")
    with patch.object(bot, "_run_var_batch", new_callable=AsyncMock) as mock_run:
        await bot.cmd_var_help(msg)
    assert "muy corto" in msg.answer.call_args.args[0]
    mock_run.assert_not_awaited()


# ---------------------------------------------------------------------------
# Entry handlers
# ---------------------------------------------------------------------------
async def test_cmd_var_photo_invalid_prompt(sessions_file, variables_file):
    msg = _make_photo_message(caption="/var")
    with patch.object(bot, "_run_var_batch", new_callable=AsyncMock) as mock_run:
        await bot.cmd_var_photo(msg)
    assert "Uso:" in msg.answer.call_args.args[0]
    mock_run.assert_not_awaited()


async def test_cmd_var_photo_downloads_and_runs(sessions_file, variables_file):
    msg = _make_photo_message(caption="/var de pie, frontal", file_id="p9")
    image = BytesIO(b"fake-image")
    with patch.object(bot, "_download_telegram_file_id", new_callable=AsyncMock, return_value=image) as mock_dl:
        with patch.object(bot, "_run_var_batch", new_callable=AsyncMock) as mock_run:
            await bot.cmd_var_photo(msg)
    mock_dl.assert_awaited_once_with("p9")
    mock_run.assert_awaited_once_with(msg, 1, "de pie, frontal", image, None, source_file_id="p9")


async def test_cmd_var_photo_with_count(sessions_file, variables_file):
    msg = _make_photo_message(caption="/var 3 de pie", file_id="p9")
    image = BytesIO(b"fake-image")
    with patch.object(bot, "_download_telegram_file_id", new_callable=AsyncMock, return_value=image) as mock_dl:
        with patch.object(bot, "_run_var_batch", new_callable=AsyncMock) as mock_run:
            await bot.cmd_var_photo(msg)
    mock_run.assert_awaited_once_with(msg, 3, "de pie", image, None, source_file_id="p9")


async def test_cmd_var_reply_invalid_prompt(sessions_file, variables_file):
    msg = _make_reply_message(text="/var")
    with patch.object(bot, "_run_var_batch", new_callable=AsyncMock) as mock_run:
        await bot.cmd_var_reply(msg)
    assert "Uso:" in msg.answer.call_args.args[0]
    mock_run.assert_not_awaited()


async def test_cmd_var_reply_without_photo(sessions_file, variables_file):
    msg = _make_reply_message(text="/var de perfil")
    msg.reply_to_message.photo = None
    with patch.object(bot, "_run_var_batch", new_callable=AsyncMock) as mock_run:
        await bot.cmd_var_reply(msg)
    assert "Responde a una foto" in msg.answer.call_args.args[0]
    mock_run.assert_not_awaited()


async def test_cmd_var_reply_downloads_photo_when_no_kie_ref(sessions_file, variables_file):
    msg = _make_reply_message(text="/var 2 de perfil", photo_file_id="r7")
    image = BytesIO(b"fake-reply")
    with patch.object(bot, "_resolve_reply_kie_ref", return_value=None):
        with patch.object(bot, "_download_telegram_photo", new_callable=AsyncMock, return_value=image) as mock_dl:
            with patch.object(bot, "_run_var_batch", new_callable=AsyncMock) as mock_run:
                await bot.cmd_var_reply(msg)
    mock_dl.assert_awaited_once()
    mock_run.assert_awaited_once_with(msg, 2, "de perfil", image, None, source_file_id="r7")


async def test_cmd_var_reply_uses_kie_ref_for_bot_image(sessions_file, variables_file):
    msg = _make_reply_message(text="/var de perfil", photo_file_id="r7")
    ref = {"task_id": "task-abc", "index": 0}
    with patch.object(bot, "_resolve_reply_kie_ref", return_value=ref):
        with patch.object(bot, "_download_telegram_photo", new_callable=AsyncMock) as mock_dl:
            with patch.object(bot, "_run_var_batch", new_callable=AsyncMock) as mock_run:
                await bot.cmd_var_reply(msg)
    mock_dl.assert_not_awaited()
    mock_run.assert_awaited_once_with(msg, 1, "de perfil", None, ref, source_file_id=None)


# ---------------------------------------------------------------------------
# Runner: batch of N images from the inline prompt injected into the template
# ---------------------------------------------------------------------------
async def test_run_var_generates_once_with_template_rendered_prompt(sessions_file, variables_file):
    """One generation whose prompt is the template with the inline fields;
    no random combos, no lists."""
    variables_store.set_template("Pose: {pose} | Ángulo: {angle}")
    msg = _make_photo_message(caption="/var de pie, frontal")
    msg.answer.return_value = _make_status()

    async def _fake_gen(model, prompt, image_data=None, **kwargs):
        return ([RESULT_URL], None, {"task_id": "t", "index": 0, "provider": "kie"})

    with patch.object(bot, "generate_image", side_effect=_fake_gen) as mock_gen:
        with patch.object(bot, "process_image_result", new_callable=AsyncMock) as mock_res:
            with patch("variables_store.random_combination", new_callable=AsyncMock) as mock_combo:
                await bot._run_var_batch(msg, 1, "de pie, frontal", BytesIO(b"img"), None, source_file_id="p1")

    mock_gen.assert_awaited_once()
    assert mock_gen.await_args.args[1] == "Pose: de pie | Ángulo: frontal"  # template injected
    assert mock_gen.await_args.args[2] is not None
    mock_combo.assert_not_awaited()
    mock_res.assert_awaited_once()
    assert mock_res.await_args.args[1] == "Pose: de pie | Ángulo: frontal"
    assert mock_res.await_args.kwargs.get("delete_status") is False  # status reused for summary
    assert "Listo: 1/1" in msg.answer.return_value.edit_text.call_args.args[0]


async def test_run_var_single_field_lands_on_first_placeholder(sessions_file, variables_file):
    """'/var de pie' with the default '{pose}, {angle}, {action}' template renders 'de pie'."""
    msg = _make_photo_message(caption="/var de pie")
    msg.answer.return_value = _make_status()

    async def _fake_gen(model, prompt, image_data=None, **kwargs):
        return ([RESULT_URL], None, None)

    with patch.object(bot, "generate_image", side_effect=_fake_gen) as mock_gen:
        with patch.object(bot, "process_image_result", new_callable=AsyncMock) as mock_res:
            await bot._run_var_batch(msg, 1, "de pie", BytesIO(b"img"), None)

    mock_gen.assert_awaited_once()
    assert mock_gen.await_args.args[1] == "de pie"  # empty {angle} dropped with its separator
    assert mock_res.await_args.args[1] == "de pie"


async def test_run_var_batch_generates_count_images_with_same_prompt(sessions_file, variables_file):
    """'/var 3 de pie, frontal' → 3 images, all with the same rendered prompt."""
    msg = _make_photo_message(caption="/var 3 de pie, frontal")
    msg.answer.return_value = _make_status()

    async def _fake_gen(model, prompt, image_data=None, **kwargs):
        return ([RESULT_URL], None, {"task_id": f"t-{prompt}", "index": 0, "provider": "kie"})

    with patch.object(bot, "generate_image", side_effect=_fake_gen) as mock_gen:
        with patch.object(bot, "process_image_result", new_callable=AsyncMock) as mock_res:
            with patch("variables_store.random_combination", new_callable=AsyncMock) as mock_combo:
                await bot._run_var_batch(msg, 3, "de pie, frontal", BytesIO(b"img"), None, source_file_id="p1")

    assert mock_gen.await_count == 3
    prompts = [call.args[1] for call in mock_gen.await_args_list]
    assert prompts == ["de pie, frontal"] * 3  # same prompt every iteration
    mock_combo.assert_not_awaited()
    assert mock_res.await_count == 3
    for call in mock_res.await_args_list:
        assert call.kwargs.get("delete_status") is False
    last_text = msg.answer.return_value.edit_text.call_args.args[0]
    assert "Listo: 3/3" in last_text


async def test_run_var_batch_continues_on_provider_error(sessions_file, variables_file):
    """A failed item is skipped and the rest of the batch still runs."""
    msg = _make_photo_message(caption="/var 2 de pie")
    status = _make_status()
    msg.answer.return_value = status
    calls = 0

    async def _fake_gen(model, prompt, image_data=None, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return None, "Error del proveedor", None
        return ([RESULT_URL], None, None)

    with patch.object(bot, "generate_image", side_effect=_fake_gen) as mock_gen:
        with patch.object(bot, "process_image_result", new_callable=AsyncMock) as mock_res:
            await bot._run_var_batch(msg, 2, "de pie", BytesIO(b"img"), None)

    assert mock_gen.await_count == 2
    assert mock_res.await_count == 1
    fail_calls = [
        c.args[0] for c in msg.answer.await_args_list
        if c.args and c.args[0].startswith("Edición")
    ]
    assert len(fail_calls) == 1
    assert "Edición 1/2 falló" in fail_calls[0]
    last_text = status.edit_text.call_args.args[0]
    assert "1/2" in last_text
    assert "1 error" in last_text


async def test_run_var_batch_failure_notice(sessions_file, variables_file):
    """A failed item notifies 'Edición i/N falló con el siguiente prompt'."""
    msg = _make_photo_message(caption="/var 1 de pie")
    status = _make_status()
    msg.answer.return_value = status

    async def _fake_gen(model, prompt, image_data=None, **kwargs):
        return None, "Error del proveedor", None

    with patch.object(bot, "generate_image", side_effect=_fake_gen):
        with patch.object(bot, "process_image_result", new_callable=AsyncMock) as mock_res:
            await bot._run_var_batch(msg, 1, "de pie", BytesIO(b"img"), None)

    mock_res.assert_not_awaited()
    fail_calls = [
        c.args[0] for c in msg.answer.await_args_list
        if c.args and c.args[0].startswith("Edición")
    ]
    assert len(fail_calls) == 1
    assert "Edición 1/1 falló con el siguiente prompt:" in fail_calls[0]
    assert "de pie" in fail_calls[0]
    last_text = status.edit_text.call_args.args[0]
    assert "0/1" in last_text
    assert "1 error" in last_text


async def test_run_var_batch_generate_exception_skips_item(sessions_file, variables_file):
    msg = _make_photo_message(caption="/var 2 de pie")
    status = _make_status()
    msg.answer.return_value = status

    async def _fake_gen(model, prompt, image_data=None, **kwargs):
        raise RuntimeError("timeout del backend")

    with patch.object(bot, "generate_image", side_effect=_fake_gen):
        with patch.object(bot, "process_image_result", new_callable=AsyncMock) as mock_res:
            await bot._run_var_batch(msg, 2, "de pie", BytesIO(b"img"), None)

    mock_res.assert_not_awaited()
    last_text = status.edit_text.call_args.args[0]
    assert "0/2" in last_text
    assert "2 errores" in last_text


async def test_run_var_text_mode_generates_without_image(sessions_file, variables_file):
    msg = _make_text_message(text="/var de pie, frontal")
    msg.answer.return_value = _make_status()

    async def _fake_gen(model, prompt, image_data=None, **kwargs):
        return ([RESULT_URL], None, None)

    captured = {}

    async def _fake_process(output, prompt, status_msg, message, prefix, **kwargs):
        captured.update(kwargs)

    with patch.object(bot, "generate_image", side_effect=_fake_gen) as mock_gen:
        with patch.object(bot, "process_image_result", side_effect=_fake_process):
            await bot._run_var_batch(msg, 1, "de pie, frontal", None, None, mode="text")

    mock_gen.assert_awaited_once()
    assert mock_gen.await_args.args[2] is None
    regen = captured["regen_context"]
    assert regen["mode"] == "text"
    assert regen["prompt"] == "de pie, frontal"
    assert "source_file_id" not in regen
    assert captured.get("download_allowlist") == "kie"


async def test_run_var_text_mode_with_count(sessions_file, variables_file):
    msg = _make_text_message(text="/var 2 de pie")
    msg.answer.return_value = _make_status()

    async def _fake_gen(model, prompt, image_data=None, **kwargs):
        return ([RESULT_URL], None, None)

    with patch.object(bot, "generate_image", side_effect=_fake_gen) as mock_gen:
        with patch.object(bot, "process_image_result", new_callable=AsyncMock) as mock_res:
            await bot._run_var_batch(msg, 2, "de pie", None, None, mode="text")

    assert mock_gen.await_count == 2
    for call in mock_gen.await_args_list:
        assert call.args[2] is None
    assert mock_res.await_count == 2
    assert "Listo: 2/2" in msg.answer.return_value.edit_text.call_args.args[0]


async def test_run_var_edit_mode_keeps_source(sessions_file, variables_file):
    msg = _make_photo_message(caption="/var de pie", file_id="p1")
    msg.answer.return_value = _make_status()

    async def _fake_gen(model, prompt, image_data=None, **kwargs):
        return ([RESULT_URL], None, None)

    captured = {}

    async def _fake_process(output, prompt, status_msg, message, prefix, **kwargs):
        captured.update(kwargs)

    with patch.object(bot, "generate_image", side_effect=_fake_gen):
        with patch.object(bot, "process_image_result", side_effect=_fake_process):
            await bot._run_var_batch(msg, 1, "de pie", BytesIO(b"img"), None, source_file_id="p1")

    regen = captured["regen_context"]
    assert regen["mode"] == "edit"
    assert regen["source_file_id"] == "p1"


async def test_run_var_cancel_before_generation(sessions_file, variables_file):
    msg = _make_photo_message(caption="/var de pie")
    status = _make_status()
    msg.answer.return_value = status

    with patch.object(bot, "_job_cancelled", return_value=True):
        with patch.object(bot, "generate_image", new_callable=AsyncMock) as mock_gen:
            await bot._run_var_batch(msg, 1, "de pie", BytesIO(b"img"), None)

    mock_gen.assert_not_awaited()
    assert "Cancelado. Completadas 0/1" in status.edit_text.call_args.args[0]


async def test_run_var_cancel_after_generation(sessions_file, variables_file):
    msg = _make_photo_message(caption="/var de pie")
    status = _make_status()
    msg.answer.return_value = status

    async def _fake_gen(model, prompt, image_data=None, **kwargs):
        return ([RESULT_URL], None, None)

    with patch.object(bot, "generate_image", side_effect=_fake_gen):
        with patch.object(bot, "process_image_result", new_callable=AsyncMock) as mock_res:
            with patch.object(bot, "_job_cancelled", side_effect=[False, True]):
                await bot._run_var_batch(msg, 1, "de pie", BytesIO(b"img"), None)

    mock_res.assert_not_awaited()
    assert "Cancelado. Completadas 0/1" in status.edit_text.call_args.args[0]


async def test_run_var_rejects_when_job_slots_full(sessions_file, variables_file):
    uid = 1001
    for _ in range(bot.MAX_ACTIVE_JOBS_PER_USER):
        assert bot._start_job(uid, "edit") is not None
    msg = _make_photo_message(caption="/var de pie")
    with patch.object(bot, "generate_image", new_callable=AsyncMock) as mock_gen:
        await bot._run_var_batch(msg, 1, "de pie", BytesIO(b"img"), None)
    assert "3 procesos" in msg.answer.call_args.args[0]
    mock_gen.assert_not_awaited()


async def test_run_var_rejects_grok_video(sessions_file, variables_file):
    _set_user_image_config(model="grok_video")
    msg = _make_photo_message(caption="/var de pie")
    with patch.object(bot, "generate_image", new_callable=AsyncMock) as mock_gen:
        await bot._run_var_batch(msg, 1, "de pie", BytesIO(b"img"), None)
    text = msg.answer.call_args.args[0]
    assert "video" in text
    mock_gen.assert_not_awaited()


async def test_run_var_rejects_faceswap(sessions_file, variables_file):
    _set_user_image_config(model="faceswap")
    msg = _make_photo_message(caption="/var de pie")
    with patch.object(bot, "generate_image", new_callable=AsyncMock) as mock_gen:
        await bot._run_var_batch(msg, 1, "de pie", BytesIO(b"img"), None)
    text = msg.answer.call_args.args[0]
    assert "Face Swap" in text
    mock_gen.assert_not_awaited()


async def test_run_var_xai_missing_key(sessions_file, variables_file, monkeypatch):
    _set_user_image_config(provider="xai")
    monkeypatch.setattr(bot, "XAI_API_KEY", "")
    msg = _make_photo_message(caption="/var de pie")
    with patch.object(bot, "generate_image", new_callable=AsyncMock) as mock_gen:
        await bot._run_var_batch(msg, 1, "de pie", BytesIO(b"img"), None)
    assert "xAI no está disponible" in msg.answer.call_args.args[0]
    mock_gen.assert_not_awaited()


async def test_run_var_uses_xai_when_configured(sessions_file, variables_file, monkeypatch):
    _set_user_image_config(provider="xai")
    monkeypatch.setattr(bot, "KIE_API_KEY", "")
    msg = _make_photo_message(caption="/var de pie")
    msg.answer.return_value = _make_status()

    async def _fake_gen(model, prompt, image_data=None, **kwargs):
        return (["https://api.x.ai/out.png"], None, None)

    with patch.object(bot, "generate_image", side_effect=_fake_gen) as mock_gen:
        with patch.object(bot, "process_image_result", new_callable=AsyncMock) as mock_res:
            await bot._run_var_batch(msg, 1, "de pie", BytesIO(b"img"), None, source_file_id="p1")

    mock_gen.assert_awaited_once()
    assert mock_gen.await_args.args[0]["provider"] == "xai"
    assert mock_res.await_args.kwargs.get("download_allowlist") == "xai"


async def test_run_var_forwards_kie_ref(sessions_file, variables_file):
    msg = _make_photo_message(caption="/var de pie")
    msg.answer.return_value = _make_status()
    kie_ref = {"task_id": "task-abc", "index": 0}

    async def _fake_gen(model, prompt, image_data=None, **kwargs):
        return ([RESULT_URL], None, {"task_id": "t", "index": 0, "provider": "kie"})

    captured = {}

    async def _fake_process(output, prompt, status_msg, message, prefix, **kwargs):
        captured.update(kwargs)

    with patch.object(bot, "generate_image", side_effect=_fake_gen) as mock_gen:
        with patch.object(bot, "process_image_result", side_effect=_fake_process):
            await bot._run_var_batch(msg, 1, "de pie", None, kie_ref)

    assert mock_gen.await_args.args[2] is None
    assert mock_gen.await_args.kwargs.get("kie_source_ref") is kie_ref
    regen = captured["regen_context"]
    assert regen["kie_source_ref"] == {"task_id": "task-abc", "index": 0}
    assert "source_file_id" not in regen


async def test_run_var_comfyui_sends_via_comfyui(sessions_file, variables_file, monkeypatch):
    monkeypatch.setattr(bot, "COMFYUI_HOST", "1.2.3.4")
    bot.user_state[1001] = {"model": "comfyui"}
    msg = _make_photo_message(caption="/var de pie")
    msg.answer.return_value = _make_status()

    async def _fake_gen(model, prompt, image_data=None, **kwargs):
        assert model.get("provider") == "comfyui"
        return (["/tmp/comfyui_1.png"], None, None)

    with patch.object(bot, "generate_image", side_effect=_fake_gen) as mock_gen:
        with patch.object(bot, "_send_comfyui_output", new_callable=AsyncMock) as mock_send:
            with patch.object(bot, "process_image_result", new_callable=AsyncMock) as mock_proc:
                await bot._run_var_batch(msg, 1, "de pie", BytesIO(b"img"), None, source_file_id="p1")

    mock_gen.assert_awaited_once()
    mock_send.assert_awaited_once()
    mock_proc.assert_not_awaited()
    assert mock_send.await_args.args[2] == "de pie"
    assert mock_send.await_args.kwargs.get("caption_prompt") is True
    assert mock_send.await_args.kwargs.get("delete_status") is False


# ---------------------------------------------------------------------------
# Dispatcher-level routing (real aiogram handler precedence)
# ---------------------------------------------------------------------------
def _make_update(*, text=None, caption=None, photo=False, reply=False, user_id=1001, chat_id=2001):
    """Build a real aiogram Update for dispatcher routing tests."""
    from aiogram.types import Chat, Message, PhotoSize, Update, User

    chat = Chat(id=chat_id, type="private")
    user = User(id=user_id, is_bot=False, first_name="T")
    kwargs: dict = dict(message_id=1, chat=chat, from_user=user, date=0)
    if text is not None:
        kwargs["text"] = text
    if caption is not None:
        kwargs["caption"] = caption
    if photo:
        kwargs["photo"] = [PhotoSize(file_id="p1", file_unique_id="u1", width=10, height=10)]
    if reply:
        kwargs["reply_to_message"] = Message(
            message_id=2,
            chat=chat,
            from_user=user,
            date=0,
            photo=[PhotoSize(file_id="r1", file_unique_id="u2", width=10, height=10)],
        )
    return Update(update_id=1, message=Message(**kwargs))


async def test_dispatcher_photo_caption_var_routes_to_batch(sessions_file, variables_file):
    """Real dispatcher: photo + '/var texto' caption must reach cmd_var_photo."""
    with patch.object(bot, "ALLOWED_TELEGRAM_IDS", None):
        with patch.object(bot, "cmd_var_photo", new_callable=AsyncMock) as mock_photo:
            with patch.object(bot, "_process_single_photo_edit", new_callable=AsyncMock) as mock_edit:
                await bot.dp.feed_update(bot.bot, _make_update(caption="/var de pie", photo=True))
    mock_photo.assert_awaited_once()
    mock_edit.assert_not_awaited()


async def test_dispatcher_reply_var_routes_to_batch(sessions_file, variables_file):
    """Real dispatcher: reply '/var texto' must reach cmd_var_reply."""
    with patch.object(bot, "ALLOWED_TELEGRAM_IDS", None):
        with patch.object(bot, "cmd_var_reply", new_callable=AsyncMock) as mock_reply:
            await bot.dp.feed_update(bot.bot, _make_update(text="/var de perfil", reply=True))
    mock_reply.assert_awaited_once()


async def test_dispatcher_text_var_runs_text_generation(sessions_file, variables_file):
    """Real dispatcher: bare '/var texto' text runs the text generation."""
    with patch.object(bot, "ALLOWED_TELEGRAM_IDS", None):
        with patch.object(bot, "_run_var_batch", new_callable=AsyncMock) as mock_run:
            await bot.dp.feed_update(bot.bot, _make_update(text="/var de pie, frontal"))
    mock_run.assert_awaited_once()
    args = mock_run.await_args.args
    assert args[1] == 1  # count
    assert args[2] == "de pie, frontal"
    assert args[3] is None and args[4] is None
    assert mock_run.await_args.kwargs["mode"] == "text"


async def test_dispatcher_text_var_with_count_runs_text_batch(sessions_file, variables_file):
    """Real dispatcher: bare '/var 3 texto' runs the text batch with count 3."""
    with patch.object(bot, "ALLOWED_TELEGRAM_IDS", None):
        with patch.object(bot, "_run_var_batch", new_callable=AsyncMock) as mock_run:
            await bot.dp.feed_update(bot.bot, _make_update(text="/var 3 de pie"))
    mock_run.assert_awaited_once()
    args = mock_run.await_args.args
    assert args[1] == 3
    assert args[2] == "de pie"
    assert mock_run.await_args.kwargs["mode"] == "text"


async def test_dispatcher_variables_not_hijacked_by_var(sessions_file, variables_file):
    """Real dispatcher: '/variables 2' must reach the variables batch, not /var."""
    with patch.object(bot, "ALLOWED_TELEGRAM_IDS", None):
        with patch.object(bot, "_run_variables_batch", new_callable=AsyncMock) as mock_batch:
            with patch.object(bot, "_run_var_batch", new_callable=AsyncMock) as mock_var:
                await bot.dp.feed_update(bot.bot, _make_update(text="/variables 2"))
    mock_batch.assert_awaited_once()
    mock_var.assert_not_awaited()


async def test_dispatcher_regular_caption_still_edits(sessions_file, variables_file):
    """Real dispatcher: a normal edit caption is unaffected by /var."""
    with patch.object(bot, "ALLOWED_TELEGRAM_IDS", None):
        with patch.object(bot, "_process_single_photo_edit", new_callable=AsyncMock) as mock_edit:
            with patch.object(bot, "cmd_var_photo", new_callable=AsyncMock) as mock_var:
                await bot.dp.feed_update(bot.bot, _make_update(caption="cambia el fondo", photo=True))
    mock_edit.assert_awaited_once()
    mock_var.assert_not_awaited()


async def test_dispatcher_plain_text_prompt_unaffected(sessions_file, variables_file):
    """Real dispatcher: a plain generation prompt still reaches handle_text."""
    with patch.object(bot, "ALLOWED_TELEGRAM_IDS", None):
        with patch.object(bot.bot, "session", new_callable=AsyncMock) as mock_session:
            await bot.dp.feed_update(bot.bot, _make_update(text="a cat in a hat"))
    assert mock_session.await_count == 1
    method = mock_session.call_args.args[1]
    assert type(method).__name__ == "SendMessage"
    assert "a cat in a hat" in method.text
    assert "Confirmar" in str(method.reply_markup)


async def test_dispatcher_bare_var_shows_usage(sessions_file, variables_file):
    """Real dispatcher: bare '/var' text shows the usage message."""
    with patch.object(bot, "ALLOWED_TELEGRAM_IDS", None):
        with patch.object(bot.bot, "session", new_callable=AsyncMock) as mock_session:
            await bot.dp.feed_update(bot.bot, _make_update(text="/var"))
    assert mock_session.await_count == 1
    method = mock_session.call_args.args[1]
    assert type(method).__name__ == "SendMessage"
    assert "/var texto" in method.text
