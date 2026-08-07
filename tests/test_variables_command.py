"""Tests for the /variables N command: parsing, routing, and batch behavior."""

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


def _make_status():
    status = MagicMock()
    status.edit_text = AsyncMock()
    status.delete = AsyncMock()
    return status


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------
def test_parse_variables_count():
    assert bot._parse_variables_count("/variables 5") == 5
    assert bot._parse_variables_count("/variables") == 1
    assert bot._parse_variables_count("/VARIABLES 3") == 3
    assert bot._parse_variables_count("/variables 15") == bot.VARIABLES_MAX  # clamped
    assert bot._parse_variables_count("/variables@MyBot 3") == 3  # group mention
    assert bot._parse_variables_count("/variables 0") is None
    assert bot._parse_variables_count("/variables abc") is None
    assert bot._parse_variables_count("/variables 2 extra") is None
    assert bot._parse_variables_count(None) is None


def test_is_variables_command():
    assert bot._is_variables_command("/variables 5")
    assert bot._is_variables_command("  /variables 5  ")
    assert bot._is_variables_command("/VARIABLES")
    assert bot._is_variables_command("/variables@MyBot 3")
    assert not bot._is_variables_command("variables 5")
    assert not bot._is_variables_command("/s de pie")
    assert not bot._is_variables_command("/variablesfoo 2")  # not hijacked
    assert not bot._is_variables_command(None)


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------
async def test_photo_caption_routes_to_variables(sessions_file, variables_file):
    msg = _make_photo_message(caption="/variables 2")
    with patch.object(bot, "cmd_variables_photo", new_callable=AsyncMock) as mock_cmd:
        await bot.handle_photo_caption(msg)
    mock_cmd.assert_awaited_once_with(msg)


async def test_photo_caption_regular_caption_not_routed(sessions_file, variables_file):
    msg = _make_photo_message(caption="cambia el fondo a playa")
    with patch.object(bot, "cmd_variables_photo", new_callable=AsyncMock) as mock_cmd:
        with patch.object(bot, "_process_single_photo_edit", new_callable=AsyncMock) as mock_edit:
            await bot.handle_photo_caption(msg)
    mock_cmd.assert_not_awaited()
    mock_edit.assert_awaited_once()


async def test_reply_routes_to_variables(sessions_file, variables_file):
    msg = _make_reply_message(text="/variables 3")
    with patch.object(bot, "cmd_variables_reply", new_callable=AsyncMock) as mock_cmd:
        await bot.handle_reply_edit(msg)
    mock_cmd.assert_awaited_once_with(msg)


async def test_variables_help_delegates_reply_to_batch(sessions_file, variables_file):
    """The Command('variables') handler runs before handle_reply_edit; it must
    delegate replies (not show usage) so the batch still starts."""
    msg = _make_reply_message(text="/variables 3")
    with patch.object(bot, "cmd_variables_reply", new_callable=AsyncMock) as mock_cmd:
        await bot.cmd_variables_help(msg)
    mock_cmd.assert_awaited_once_with(msg)
    msg.answer.assert_not_awaited()


async def test_variables_help_delegates_photo_caption_to_batch(sessions_file, variables_file):
    """aiogram's Command filter matches captions too and this handler is
    registered before handle_photo_caption, so it must delegate photos."""
    msg = _make_photo_message(caption="/variables 2")
    with patch.object(bot, "cmd_variables_photo", new_callable=AsyncMock) as mock_cmd:
        await bot.cmd_variables_help(msg)
    mock_cmd.assert_awaited_once_with(msg)
    msg.answer.assert_not_awaited()


async def test_variables_help_photo_in_album_shows_usage(sessions_file, variables_file):
    """Album photos with a /variables caption fall back to usage, matching the
    single-image nature of the feature."""
    msg = _make_photo_message(caption="/variables 2")
    msg.media_group_id = "mg-1"
    with patch.object(bot, "cmd_variables_photo", new_callable=AsyncMock) as mock_cmd:
        await bot.cmd_variables_help(msg)
    mock_cmd.assert_not_awaited()
    assert "Gestiona las listas con" in msg.answer.call_args.args[0]


async def test_variables_help_bare_text_shows_usage(sessions_file, variables_file):
    msg = _make_reply_message(text="/variables")
    msg.reply_to_message = None
    await bot.cmd_variables_help(msg)
    assert "Gestiona las listas con" in msg.answer.call_args.args[0]


# ---------------------------------------------------------------------------
# Entry handlers
# ---------------------------------------------------------------------------
async def test_cmd_variables_photo_invalid_count(sessions_file, variables_file):
    msg = _make_photo_message(caption="/variables abc")
    with patch.object(bot, "_run_variables_batch", new_callable=AsyncMock) as mock_batch:
        await bot.cmd_variables_photo(msg)
    assert "Uso:" in msg.answer.call_args.args[0]
    mock_batch.assert_not_awaited()


async def test_cmd_variables_photo_downloads_and_runs_batch(sessions_file, variables_file):
    msg = _make_photo_message(caption="/variables 2", file_id="p9")
    image = BytesIO(b"fake-image")
    with patch.object(bot, "_download_telegram_file_id", new_callable=AsyncMock, return_value=image) as mock_dl:
        with patch.object(bot, "_run_variables_batch", new_callable=AsyncMock) as mock_batch:
            await bot.cmd_variables_photo(msg)
    mock_dl.assert_awaited_once_with("p9")
    mock_batch.assert_awaited_once_with(msg, 2, image, None, source_file_id="p9")


async def test_cmd_variables_reply_invalid_count(sessions_file, variables_file):
    msg = _make_reply_message(text="/variables 0")
    with patch.object(bot, "_run_variables_batch", new_callable=AsyncMock) as mock_batch:
        await bot.cmd_variables_reply(msg)
    assert "Uso:" in msg.answer.call_args.args[0]
    mock_batch.assert_not_awaited()


async def test_cmd_variables_reply_downloads_photo_when_no_kie_ref(sessions_file, variables_file):
    msg = _make_reply_message(text="/variables 2", photo_file_id="r7")
    image = BytesIO(b"fake-reply")
    with patch.object(bot, "_resolve_reply_kie_ref", return_value=None):
        with patch.object(bot, "_download_telegram_photo", new_callable=AsyncMock, return_value=image) as mock_dl:
            with patch.object(bot, "_run_variables_batch", new_callable=AsyncMock) as mock_batch:
                await bot.cmd_variables_reply(msg)
    mock_dl.assert_awaited_once()
    mock_batch.assert_awaited_once_with(msg, 2, image, None, source_file_id="r7")


async def test_cmd_variables_reply_uses_kie_ref_for_bot_image(sessions_file, variables_file):
    msg = _make_reply_message(text="/variables 2", photo_file_id="r7")
    ref = {"task_id": "task-abc", "index": 0}
    with patch.object(bot, "_resolve_reply_kie_ref", return_value=ref):
        with patch.object(bot, "_download_telegram_photo", new_callable=AsyncMock) as mock_dl:
            with patch.object(bot, "_run_variables_batch", new_callable=AsyncMock) as mock_batch:
                await bot.cmd_variables_reply(msg)
    mock_dl.assert_not_awaited()
    mock_batch.assert_awaited_once_with(msg, 2, None, ref, source_file_id=None)


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


async def test_dispatcher_photo_caption_variables_routes_to_batch(sessions_file, variables_file):
    """Real dispatcher: photo + '/variables 2' caption must reach the batch
    (via Command-filter delegation), not the normal edit path."""
    with patch.object(bot, "ALLOWED_TELEGRAM_IDS", None):
        with patch.object(bot, "cmd_variables_photo", new_callable=AsyncMock) as mock_photo:
            with patch.object(bot, "_process_single_photo_edit", new_callable=AsyncMock) as mock_edit:
                await bot.dp.feed_update(bot.bot, _make_update(caption="/variables 2", photo=True))
    mock_photo.assert_awaited_once()
    mock_edit.assert_not_awaited()


async def test_dispatcher_reply_variables_routes_to_batch(sessions_file, variables_file):
    """Real dispatcher: reply '/variables 3' must reach the reply batch."""
    with patch.object(bot, "ALLOWED_TELEGRAM_IDS", None):
        with patch.object(bot, "cmd_variables_reply", new_callable=AsyncMock) as mock_reply:
            await bot.dp.feed_update(bot.bot, _make_update(text="/variables 3", reply=True))
    mock_reply.assert_awaited_once()


async def test_dispatcher_bare_variables_shows_usage(sessions_file, variables_file):
    """Real dispatcher: bare '/variables' text reaches the help handler, which
    answers the usage text (the registered handler cannot be patched by module
    attribute, so we intercept the underlying API session call)."""
    with patch.object(bot, "ALLOWED_TELEGRAM_IDS", None):
        with patch.object(bot.bot, "session", new_callable=AsyncMock) as mock_session:
            await bot.dp.feed_update(bot.bot, _make_update(text="/variables"))
    assert mock_session.await_count == 1
    method = mock_session.call_args.args[1]
    assert type(method).__name__ == "SendMessage"
    assert "Gestiona las listas con" in method.text


async def test_dispatcher_regular_caption_still_edits(sessions_file, variables_file):
    """Real dispatcher: a normal edit caption is unaffected by the new feature."""
    with patch.object(bot, "ALLOWED_TELEGRAM_IDS", None):
        with patch.object(bot, "_process_single_photo_edit", new_callable=AsyncMock) as mock_edit:
            with patch.object(bot, "cmd_variables_photo", new_callable=AsyncMock) as mock_photo:
                await bot.dp.feed_update(bot.bot, _make_update(caption="cambia el fondo", photo=True))
    mock_edit.assert_awaited_once()
    mock_photo.assert_not_awaited()


async def test_dispatcher_plain_text_prompt_unaffected(sessions_file, variables_file):
    """Real dispatcher: a plain generation prompt still reaches the text
    generation path (handle_text shows the confirmation prompt)."""
    with patch.object(bot, "ALLOWED_TELEGRAM_IDS", None):
        with patch.object(bot.bot, "session", new_callable=AsyncMock) as mock_session:
            await bot.dp.feed_update(bot.bot, _make_update(text="a cat in a hat"))
    assert mock_session.await_count == 1
    method = mock_session.call_args.args[1]
    assert type(method).__name__ == "SendMessage"
    assert "a cat in a hat" in method.text
    assert "Confirmar" in str(method.reply_markup)


def _make_real_message(*, text=None, chat_type="private", user_id=1001, chat_id=2001, reply_photo=False):
    """Build a real aiogram Message for direct handler-call tests."""
    from aiogram.types import Chat, Message, PhotoSize, User

    chat = Chat(id=chat_id, type=chat_type)
    user = User(id=user_id, is_bot=False, first_name="T")
    kwargs: dict = dict(message_id=1, chat=chat, from_user=user, date=0, text=text)
    if reply_photo:
        kwargs["reply_to_message"] = Message(
            message_id=2,
            chat=chat,
            from_user=user,
            date=0,
            photo=[PhotoSize(file_id="r1", file_unique_id="u2", width=10, height=10)],
        )
    return Message(**kwargs)


async def _set_variables_fsm(*, state_name, data):
    """Set the real FSM state+data for user 1001 / chat 2001."""
    from aiogram.fsm.storage.base import StorageKey

    key = StorageKey(chat_id=2001, user_id=1001, bot_id=bot.bot.id)
    storage = bot.dp.storage
    await storage.set_state(key, bot.variables_flow._state_key(getattr(bot.variables_flow.VarStates, state_name)))
    await storage.set_data(key, data)
    return key, storage


async def test_handle_text_defensive_guard_delegates_to_panel(sessions_file, variables_file):
    """Even if handle_text runs first (registration regression), text sent in
    the panel's add state must reach the panel, never the confirmation."""
    key, storage = await _set_variables_fsm(
        state_name="add_item",
        data={"vars_list": "poses", "vars_message_id": 5, "vars_chat_id": 2001},
    )
    try:
        with patch.object(bot, "ALLOWED_TELEGRAM_IDS", None):
            with patch.dict(
                bot._VARS_DEPS,
                {"allowed_telegram_ids": None, "variables_admin_ids": None},
            ):
                with patch.object(bot.bot, "session", new_callable=AsyncMock) as mock_session:
                    msg = _make_real_message(text="volando").as_(bot.bot)
                    await bot.handle_text(msg)
        assert "volando" in variables_store.get_list("poses")
        texts = [
            c.args[1].text or ""
            for c in mock_session.call_args_list
            if type(c.args[1]).__name__ == "SendMessage"
        ]
        assert any("volando" in t for t in texts)
        assert not any("Confirmar" in t for t in texts)
    finally:
        await storage.set_state(key, None)


async def test_handle_reply_edit_defensive_guard_delegates_to_panel(sessions_file, variables_file):
    """Same defensive guarantee for the reply-edit path (edit state)."""
    key, storage = await _set_variables_fsm(
        state_name="edit_text",
        data={"vars_list": "poses", "vars_index": 0, "vars_message_id": 5, "vars_chat_id": 2001},
    )
    try:
        with patch.object(bot, "ALLOWED_TELEGRAM_IDS", None):
            with patch.dict(
                bot._VARS_DEPS,
                {"allowed_telegram_ids": None, "variables_admin_ids": None},
            ):
                with patch.object(bot.bot, "session", new_callable=AsyncMock) as mock_session:
                    msg = _make_real_message(text="tumbado", reply_photo=True).as_(bot.bot)
                    await bot.handle_reply_edit(msg)
        items = variables_store.get_list("poses")
        assert items[0] == "tumbado"
        texts = [
            c.args[1].text or ""
            for c in mock_session.call_args_list
            if type(c.args[1]).__name__ == "SendMessage"
        ]
        assert any("tumbado" in t for t in texts)
    finally:
        await storage.set_state(key, None)


async def test_dispatcher_panel_add_flow_end_to_end(sessions_file, variables_file):
    """The real user flow through the dispatcher: /listas panel open, click
    'Añadir', then type the item — it must reach the panel, never the image
    generation confirmation."""
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.types import CallbackQuery, Chat, Message, Update, User

    key = StorageKey(chat_id=2001, user_id=1001, bot_id=bot.bot.id)
    storage = bot.dp.storage
    await storage.set_state(key, bot.variables_flow._state_key(bot.variables_flow.VarStates.menu))
    await storage.set_data(key, {"vars_message_id": 5, "vars_chat_id": 2001})
    try:
        chat = Chat(id=2001, type="private")
        user = User(id=1001, is_bot=False, first_name="T")
        panel = Message(message_id=5, chat=chat, from_user=user, date=0, text="panel")
        with patch.object(bot, "ALLOWED_TELEGRAM_IDS", None):
            with patch.dict(
                bot._VARS_DEPS,
                {"allowed_telegram_ids": None, "variables_admin_ids": None},
            ):
                with patch.object(bot.bot, "session", new_callable=AsyncMock) as mock_session:
                    # 1) admin clicks '➕ Añadir' on the poses list
                    cq = CallbackQuery(
                        id="cq1",
                        from_user=user,
                        chat_instance="ci-1",
                        message=panel,
                        data="var:add:poses",
                    )
                    await bot.dp.feed_update(bot.bot, Update(update_id=2, callback_query=cq))
                    assert await storage.get_state(key) == bot.variables_flow._state_key(
                        bot.variables_flow.VarStates.add_item
                    )
                    # 2) admin types the new item
                    await bot.dp.feed_update(bot.bot, _make_update(text="volando"))
        assert "volando" in variables_store.get_list("poses")
        assert variables_store.get_list("poses").count("volando") == 1  # no double-add
        sent_texts = [
            c.args[1].text or ""
            for c in mock_session.call_args_list
            if type(c.args[1]).__name__ == "SendMessage"
        ]
        assert not any("Confirmar" in t for t in sent_texts)
    finally:
        await storage.set_state(key, None)


# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------
async def test_batch_generates_count_images_with_distinct_prompts(sessions_file, variables_file):
    msg = _make_photo_message(caption="/variables 3")
    msg.answer.return_value = _make_status()
    combos = [
        ("de pie, frontal, mirando", ("de pie", "frontal", "mirando")),
        ("sentado, lateral, saltando", ("sentado", "lateral", "saltando")),
        ("acostado, cenital, durmiendo", ("acostado", "cenital", "durmiendo")),
    ]

    async def _fake_gen(model, prompt, image_data=None, **kwargs):
        return ([RESULT_URL], None, {"task_id": f"t-{prompt}", "index": 0, "provider": "kie"})

    with patch.object(bot, "generate_image", side_effect=_fake_gen) as mock_gen:
        with patch.object(bot, "process_image_result", new_callable=AsyncMock) as mock_res:
            with patch(
                "variables_store.random_combination",
                side_effect=combos,
            ):
                await bot._run_variables_batch(msg, 3, BytesIO(b"img"), None, source_file_id="p1")

    assert mock_gen.await_count == 3
    prompts = [call.args[1] for call in mock_gen.await_args_list]
    assert len(set(prompts)) == 3
    # model forced to Kie provider for every call
    for call in mock_gen.await_args_list:
        assert call.args[0]["provider"] == "kie"
    assert mock_res.await_count == 3
    # status ends with summary
    last_text = msg.answer.return_value.edit_text.call_args.args[0]
    assert "Listo: 3/3" in last_text


async def test_batch_forces_kie_and_reuses_original_image(sessions_file, variables_file):
    """Each iteration must pass the same original image (never the result)."""
    msg = _make_photo_message(caption="/variables 2")
    msg.answer.return_value = _make_status()
    original = BytesIO(b"original")
    seen_images = []

    async def _fake_gen(model, prompt, image_data=None, **kwargs):
        seen_images.append(image_data)
        return ([RESULT_URL], None, {"task_id": "t", "index": 0, "provider": "kie"})

    combos = [
        ("a, b, c", ("a", "b", "c")),
        ("d, e, f", ("d", "e", "f")),
    ]
    with patch.object(bot, "generate_image", side_effect=_fake_gen):
        with patch.object(bot, "process_image_result", new_callable=AsyncMock):
            with patch("variables_store.random_combination", side_effect=combos):
                await bot._run_variables_batch(msg, 2, original, None)

    assert seen_images == [original, original]


async def test_batch_stops_on_provider_error(sessions_file, variables_file):
    msg = _make_photo_message(caption="/variables 3")
    status = _make_status()
    msg.answer.return_value = status

    async def _fake_gen(model, prompt, image_data=None, **kwargs):
        if prompt.startswith("fail"):
            return None, "Error del proveedor", None
        return ([RESULT_URL], None, {"task_id": "t", "index": 0, "provider": "kie"})

    combos = [
        ("ok1, b, c", ("ok1", "b", "c")),
        ("fail, e, f", ("fail", "e", "f")),
        ("nunca, h, i", ("nunca", "h", "i")),
    ]
    with patch.object(bot, "generate_image", side_effect=_fake_gen) as mock_gen:
        with patch.object(bot, "process_image_result", new_callable=AsyncMock) as mock_res:
            with patch("variables_store.random_combination", side_effect=combos):
                await bot._run_variables_batch(msg, 3, BytesIO(b"img"), None)

    assert mock_gen.await_count == 2
    assert mock_res.await_count == 1
    err_text = status.edit_text.call_args.args[0]
    assert "Error del proveedor" in err_text


async def test_batch_empty_list_guard(sessions_file, variables_file):
    msg = _make_photo_message(caption="/variables 2")
    empty_lists = {"poses": [], "angles": ["a"], "actions": ["b"]}
    with patch("variables_store.get_lists", return_value=empty_lists):
        with patch.object(bot, "generate_image", new_callable=AsyncMock) as mock_gen:
            await bot._run_variables_batch(msg, 2, BytesIO(b"img"), None)
    assert "está vacía" in msg.answer.call_args.args[0]
    mock_gen.assert_not_awaited()


async def test_batch_cancel_stops_after_completed(sessions_file, variables_file):
    msg = _make_photo_message(caption="/variables 5")
    status = _make_status()
    msg.answer.return_value = status

    async def _fake_gen(model, prompt, image_data=None, **kwargs):
        return ([RESULT_URL], None, {"task_id": "t", "index": 0, "provider": "kie"})

    combos = [
        ("a1, b1, c1", ("a1", "b1", "c1")),
        ("a2, b2, c2", ("a2", "b2", "c2")),
        ("a3, b3, c3", ("a3", "b3", "c3")),
    ]
    # checks: pre/post-1(False,False), pre/post-2(False,False), pre-3(True → cancel)
    with patch.object(bot, "generate_image", side_effect=_fake_gen):
        with patch.object(bot, "process_image_result", new_callable=AsyncMock):
            with patch("variables_store.random_combination", side_effect=combos):
                with patch.object(
                    bot,
                    "_job_cancelled",
                    side_effect=[False, False, False, False, True],
                ):
                    await bot._run_variables_batch(msg, 5, BytesIO(b"img"), None)

    assert "Cancelado. Completadas 2/5" in status.edit_text.call_args.args[0]


async def test_batch_no_kie_api_key(sessions_file, variables_file, monkeypatch):
    msg = _make_photo_message(caption="/variables 2")
    monkeypatch.setattr(bot, "KIE_API_KEY", "")
    with patch.object(bot, "generate_image", new_callable=AsyncMock) as mock_gen:
        await bot._run_variables_batch(msg, 2, BytesIO(b"img"), None)
    assert "Kie.ai" in msg.answer.call_args.args[0]
    mock_gen.assert_not_awaited()


async def test_batch_regen_context_has_kie_provider(sessions_file, variables_file):
    """process_image_result receives a regen context pointing at the Kie model."""
    msg = _make_photo_message(caption="/variables 1")
    msg.answer.return_value = _make_status()

    async def _fake_gen(model, prompt, image_data=None, **kwargs):
        return ([RESULT_URL], None, {"task_id": "t", "index": 0, "provider": "kie"})

    captured = {}

    async def _fake_process(output, prompt, status_msg, message, prefix, **kwargs):
        captured.update(kwargs)

    with patch.object(bot, "generate_image", side_effect=_fake_gen):
        with patch.object(bot, "process_image_result", side_effect=_fake_process):
            with patch(
                "variables_store.random_combination",
                return_value=("de pie, frontal, mirando", ("de pie", "frontal", "mirando")),
            ):
                await bot._run_variables_batch(msg, 1, BytesIO(b"img"), None, source_file_id="p1")

    regen = captured["regen_context"]
    assert regen["provider"] == "kie"
    assert regen["source_file_id"] == "p1"
    assert regen["prompt"] == "de pie, frontal, mirando"
