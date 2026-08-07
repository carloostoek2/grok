"""Tests for the /listas admin panel (variables_flow): CRUD flows + guards."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bot
import variables_flow
import variables_store


@pytest.fixture(autouse=True)
def open_variables_panel(monkeypatch):
    """Tests assume the panel is open (no allowlist/admin env leaks from .env).
    Admin-restriction tests override these values explicitly."""
    monkeypatch.setitem(bot._VARS_DEPS, "variables_admin_ids", None)
    monkeypatch.setitem(bot._VARS_DEPS, "allowed_telegram_ids", None)


def _real_update(*, text=None):
    """Build a real aiogram Update with a plain text message."""
    from aiogram.types import Chat, Message, Update, User

    chat = Chat(id=2001, type="private")
    user = User(id=1001, is_bot=False, first_name="T")
    msg = Message(message_id=1, chat=chat, from_user=user, date=0, text=text)
    return Update(update_id=1, message=msg)


def _make_message(*, text=None, chat_type="private", user_id=1001, message_id=1):
    msg = MagicMock()
    msg.text = text
    msg.from_user.id = user_id
    msg.chat.type = chat_type
    msg.chat.id = 2001
    msg.message_id = message_id
    msg.answer = AsyncMock()
    # fresh panel returned by answer()
    panel = MagicMock()
    panel.message_id = 50
    panel.chat.id = 2001
    msg.answer.return_value = panel
    # bot handles for best-effort panel cleanup
    msg.bot = MagicMock()
    msg.bot.delete_message = AsyncMock()
    return msg


def _make_callback(*, data, chat_type="private", user_id=1001, message_id=5):
    cb = MagicMock()
    cb.data = data
    cb.from_user.id = user_id
    cb.message.chat.type = chat_type
    cb.message.chat.id = 2001
    cb.message.message_id = message_id
    cb.answer = AsyncMock()
    return cb


def _make_state(state_name: str = "menu", **data):
    state = MagicMock()
    state.set_state = AsyncMock()
    state.update_data = AsyncMock()
    state.get_data = AsyncMock(return_value=data)
    state.clear = AsyncMock()
    state.get_state = AsyncMock(
        return_value=variables_flow._state_key(getattr(variables_flow.VarStates, state_name))
    )
    return state


# ---------------------------------------------------------------------------
# /listas command
# ---------------------------------------------------------------------------
async def test_cmd_listas_shows_menu(variables_file, mock_vars_safe_edit):
    msg = _make_message()
    await variables_flow.cmd_listas(msg, _make_state())
    text = msg.answer.call_args.args[0]
    assert "Poses" in text
    assert "Ángulos" in text
    assert "Acciones" in text
    assert "{pose}, {angle}, {action}" in text
    kb = msg.answer.call_args.kwargs["reply_markup"]
    callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "var:open:poses" in callbacks
    assert "var:open:angles" in callbacks
    assert "var:open:actions" in callbacks


async def test_cmd_listas_first_tap_not_rejected(variables_file, mock_vars_safe_edit):
    """After /listas the FSM must be in VarStates.menu with a stored message id,
    otherwise the very first button tap would be rejected as stale."""
    state = MagicMock()
    state.set_state = AsyncMock()
    state.update_data = AsyncMock()
    state.clear = AsyncMock()
    state.get_state = AsyncMock(return_value=None)  # post-clear
    msg = _make_message()
    panel = MagicMock()
    panel.message_id = 7
    panel.chat.id = 2001
    msg.answer.return_value = panel
    await variables_flow.cmd_listas(msg, state)
    state.set_state.assert_awaited_once_with(variables_flow.VarStates.menu)
    state.update_data.assert_awaited_once()

    # First tap: state now menu + stored message id → passes the stale guard.
    cb = _make_callback(data="var:open:poses", message_id=7)
    tapped = MagicMock()
    tapped.set_state = AsyncMock()
    tapped.update_data = AsyncMock()
    tapped.clear = AsyncMock()
    tapped.get_data = AsyncMock(return_value={"vars_message_id": 7})
    tapped.get_state = AsyncMock(
        return_value=variables_flow._state_key(variables_flow.VarStates.menu)
    )
    await variables_flow.handle_var_open(cb, tapped)
    # success path answers with no args; the stale-rejection would show an alert
    assert cb.answer.call_count == 1
    assert not cb.answer.call_args.args


async def test_cmd_listas_rejected_in_group_chat(variables_file):
    msg = _make_message(chat_type="supergroup")
    await variables_flow.cmd_listas(msg, _make_state())
    assert "chats privados" in msg.answer.call_args.args[0]


async def test_cmd_listas_rejected_for_non_admin(variables_file, monkeypatch):
    """With VARIABLES_ADMIN_IDS configured, non-admins cannot open the panel."""
    monkeypatch.setitem(bot._VARS_DEPS, "variables_admin_ids", {999})
    msg = _make_message(user_id=1001)
    await variables_flow.cmd_listas(msg, _make_state())
    assert "No tienes permiso" in msg.answer.call_args.args[0]


async def test_cmd_listas_allowed_for_configured_admin(variables_file, monkeypatch):
    monkeypatch.setitem(bot._VARS_DEPS, "variables_admin_ids", {1001})
    msg = _make_message(user_id=1001)
    await variables_flow.cmd_listas(msg, _make_state())
    assert "Listas de variables" in msg.answer.call_args.args[0]


async def test_callback_rejected_for_non_admin(variables_file, monkeypatch, mock_vars_safe_edit):
    monkeypatch.setitem(bot._VARS_DEPS, "variables_admin_ids", {999})
    cb = _make_callback(data="var:open:poses", user_id=1001)
    await variables_flow.handle_var_open(cb, _make_state())
    cb.answer.assert_called_once()
    assert "No tienes permiso" in cb.answer.call_args.args[0]


# ---------------------------------------------------------------------------
# List screen / open
# ---------------------------------------------------------------------------
async def test_handle_var_open_shows_list_screen(variables_file, mock_vars_safe_edit):
    cb = _make_callback(data="var:open:poses")
    await variables_flow.handle_var_open(cb, _make_state())
    text = mock_vars_safe_edit.call_args.args[1]
    assert "Poses" in text
    assert "1. de pie" in text
    kb = mock_vars_safe_edit.call_args.kwargs["reply_markup"]
    callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "var:add:poses" in callbacks
    assert "var:edit:poses" in callbacks
    assert "var:del:poses" in callbacks


async def test_handle_var_open_unknown_list_rejected(variables_file, mock_vars_safe_edit):
    cb = _make_callback(data="var:open:nope")
    await variables_flow.handle_var_open(cb, _make_state())
    cb.answer.assert_called_with("Lista no válida.", show_alert=True)


# ---------------------------------------------------------------------------
# Add flow
# ---------------------------------------------------------------------------
async def test_add_flow_requests_text_then_adds(variables_file, mock_vars_safe_edit):
    cb = _make_callback(data="var:add:poses")
    state = _make_state()
    await variables_flow.handle_var_add(cb, state)
    state.set_state.assert_awaited_once_with(variables_flow.VarStates.add_item)
    state.update_data.assert_awaited_once()

    msg = _make_message(text="volando")
    await variables_flow.handle_add_text(
        msg,
        _make_state("add_item", vars_list="poses", vars_message_id=5, vars_chat_id=2001),
    )
    assert "volando" in variables_store.get_list("poses")
    # fresh bot panel sent (never edits the user message), old panel deleted
    text = msg.answer.call_args.args[0]
    assert "volando" in text
    msg.bot.delete_message.assert_awaited_once()


async def test_add_duplicate_keeps_prompt(variables_file, mock_vars_safe_edit):
    msg = _make_message(text="de pie")
    await variables_flow.handle_add_text(msg, _make_state("add_item", vars_list="poses"))
    assert "ya existe" in msg.answer.call_args.args[0]


async def test_add_long_item_without_limit(variables_file, mock_vars_safe_edit):
    """The panel accepts items longer than 200 chars (display stays truncated)."""
    long_text = (
        "Peso totalmente apoyado en la pierna trasera, pierna delantera relajada y "
        "cruzada levemente, hombros inclinados en ángulo opuesto a la cadera, torso "
        "girado 45 grados respecto a la cámara. "
    ).strip() * 2
    assert len(long_text) > 200
    msg = _make_message(text=long_text)
    await variables_flow.handle_add_text(
        msg,
        _make_state("add_item", vars_list="poses", vars_message_id=5, vars_chat_id=2001),
    )
    # full item persisted
    assert long_text in variables_store.get_list("poses")
    # panel refreshed (list screen), not an error message
    text = msg.answer.call_args.args[0]
    assert "Poses" in text
    assert "demasiado" not in text


async def test_add_invalid_short_text(variables_file):
    msg = _make_message(text="a")
    await variables_flow.handle_add_text(msg, _make_state("add_item", vars_list="poses"))
    assert "corto" in msg.answer.call_args.args[0]


# ---------------------------------------------------------------------------
# Edit flow
# ---------------------------------------------------------------------------
async def test_edit_flow_picks_item_then_updates(variables_file, mock_vars_safe_edit):
    cb = _make_callback(data="var:edit:poses")
    await variables_flow.handle_var_edit_list(cb, _make_state())
    # item picker shown
    text = mock_vars_safe_edit.call_args.args[1]
    assert "Editar Poses" in text
    picker_kb = mock_vars_safe_edit.call_args.kwargs["reply_markup"]
    picker_callbacks = [btn.callback_data for row in picker_kb.inline_keyboard for btn in row]
    assert "var:item:edit:poses:0" in picker_callbacks

    cb2 = _make_callback(data="var:item:edit:poses:0")
    state = _make_state()
    await variables_flow.handle_var_item_edit(cb2, state)
    state.set_state.assert_awaited_once_with(variables_flow.VarStates.edit_text)
    state.update_data.assert_awaited_once()

    msg = _make_message(text="tumbado")
    await variables_flow.handle_edit_text(
        msg,
        _make_state("edit_text", vars_list="poses", vars_index=0),
    )
    items = variables_store.get_list("poses")
    assert items[0] == "tumbado"
    assert "de pie" not in items
    text = msg.answer.call_args.args[0]
    assert "tumbado" in text


async def test_edit_out_of_range_alert(variables_file, mock_vars_safe_edit):
    cb = _make_callback(data="var:item:edit:poses:999")
    await variables_flow.handle_var_item_edit(cb, _make_state())
    cb.answer.assert_called_with("El elemento ya no existe.", show_alert=True)


# ---------------------------------------------------------------------------
# Delete flow
# ---------------------------------------------------------------------------
async def test_delete_flow_removes_item(variables_file, mock_vars_safe_edit):
    cb = _make_callback(data="var:del:poses")
    await variables_flow.handle_var_del_list(cb, _make_state())
    picker_kb = mock_vars_safe_edit.call_args.kwargs["reply_markup"]
    picker_callbacks = [btn.callback_data for row in picker_kb.inline_keyboard for btn in row]
    assert "var:item:del:poses:0" in picker_callbacks

    before = variables_store.get_list("poses")
    cb2 = _make_callback(data="var:item:del:poses:0")
    await variables_flow.handle_var_item_del(cb2, _make_state())
    after = variables_store.get_list("poses")
    assert len(after) == len(before) - 1
    assert before[0] not in after
    # refreshed list screen shown
    text = mock_vars_safe_edit.call_args.args[1]
    assert "Poses" in text


async def test_delete_empty_list_alert(variables_file, mock_vars_safe_edit):
    while variables_store.delete_item("poses", 0):
        pass
    cb = _make_callback(data="var:del:poses")
    await variables_flow.handle_var_del_list(cb, _make_state())
    cb.answer.assert_called_with("La lista está vacía. Añade elementos primero.", show_alert=True)


# ---------------------------------------------------------------------------
# Template flow
# ---------------------------------------------------------------------------
async def test_template_flow_saves_new_template(variables_file, mock_vars_safe_edit):
    cb = _make_callback(data="var:tmpl")
    state = _make_state()
    await variables_flow.handle_var_tmpl(cb, state)
    state.set_state.assert_awaited_once_with(variables_flow.VarStates.template)

    msg = _make_message(text="El sujeto está {pose} con {angle} y {action}")
    await variables_flow.handle_template_text(msg, _make_state("template"))
    assert variables_store.get_template() == "El sujeto está {pose} con {angle} y {action}"
    # back to the menu (fresh panel)
    text = msg.answer.call_args.args[0]
    assert "Listas de variables" in text


async def test_template_flow_accepts_long_template(variables_file, mock_vars_safe_edit):
    """Templates longer than 500 chars are accepted and saved in full."""
    long_template = ("El sujeto está {pose} con {angle} y {action}, además " * 20).strip()
    assert len(long_template) > 500
    msg = _make_message(text=long_template)
    await variables_flow.handle_template_text(
        msg,
        _make_state("template", vars_message_id=5, vars_chat_id=2001),
    )
    assert variables_store.get_template() == long_template
    text = msg.answer.call_args.args[0]
    assert "Listas de variables" in text
    assert "demasiado" not in text


def test_menu_truncates_long_template_display(variables_file):
    """The menu shows a truncated template so a huge one can't break the panel."""
    variables_store.set_template("x" * 500)
    text = variables_flow._menu_text()
    assert "x" * 79 + "…" in text
    assert "x" * 80 not in text


def test_list_text_caps_display(variables_file):
    """Long lists render a capped preview so the message stays under the
    Telegram text limit."""
    for i in range(40):
        variables_store.add_item("poses", f"opción extra {i}")
    text = variables_flow._list_text("poses")
    assert "y 16 más" in text  # 6 defaults + 40 added = 46; 30 shown → 16 hidden
    assert "opción extra 39" not in text  # beyond the cap is not rendered
    assert "1. de pie" in text


# ---------------------------------------------------------------------------
# Cancel / back / close
# ---------------------------------------------------------------------------
async def test_handle_var_cancel_returns_to_list(variables_file, mock_vars_safe_edit):
    cb = _make_callback(data="var:cancel")
    state = _make_state("add_item", vars_list="poses")
    await variables_flow.handle_var_cancel(cb, state)
    text = mock_vars_safe_edit.call_args.args[1]
    assert "Poses" in text


async def test_handle_var_cancel_stale_ignored(variables_file, mock_vars_safe_edit):
    """A stale cancel button (panel already on the menu) must not navigate."""
    cb = _make_callback(data="var:cancel")
    await variables_flow.handle_var_cancel(cb, _make_state("menu", vars_list="poses"))
    mock_vars_safe_edit.assert_not_awaited()


async def test_handle_var_back_returns_to_menu(variables_file, mock_vars_safe_edit):
    cb = _make_callback(data="var:back")
    await variables_flow.handle_var_back(cb, _make_state())
    text = mock_vars_safe_edit.call_args.args[1]
    assert "Listas de variables" in text


async def test_handle_var_close_clears_state(variables_file, mock_vars_safe_edit):
    cb = _make_callback(data="var:close")
    state = _make_state()
    await variables_flow.handle_var_close(cb, state)
    state.clear.assert_awaited_once()
    assert "cerrado" in mock_vars_safe_edit.call_args.args[1]


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------
async def test_callback_rejected_in_group_chat(variables_file, mock_vars_safe_edit):
    cb = _make_callback(data="var:open:poses", chat_type="group")
    await variables_flow.handle_var_open(cb, _make_state())
    cb.answer.assert_called_once()
    assert "privados" in cb.answer.call_args.args[0]


async def test_stale_callback_rejected(variables_file, mock_vars_safe_edit):
    cb = _make_callback(data="var:open:poses")
    state = _make_state("add_item", vars_list="poses")
    await variables_flow.handle_var_open(cb, state)
    cb.answer.assert_called_once()
    assert "ya no está activa" in cb.answer.call_args.args[0]


async def test_malformed_item_callback_rejected(variables_file, mock_vars_safe_edit):
    """Crafted 'var:item:edit:poses' (no index) must not crash the handler."""
    cb = _make_callback(data="var:item:edit:poses")
    await variables_flow.handle_var_item_edit(cb, _make_state())
    cb.answer.assert_called_once()
    assert "no válida" in cb.answer.call_args.args[0]
    cb2 = _make_callback(data="var:item:del:poses")
    await variables_flow.handle_var_item_del(cb2, _make_state())
    cb2.answer.assert_called_once()


async def test_text_input_rejected_in_group(variables_file, mock_vars_safe_edit):
    """A flow started in a private chat cannot be completed from a group."""
    msg = _make_message(text="volando", chat_type="group")
    await variables_flow.handle_add_text(msg, _make_state("add_item", vars_list="poses"))
    assert "chats privados" in msg.answer.call_args.args[0]
    assert "volando" not in variables_store.get_list("poses")


async def test_dispatcher_fsm_text_input_wins_over_generic_handlers(
    sessions_file, variables_file
):
    """Real dispatcher: while in VarStates.add_item, typed text must reach
    handle_add_text (registered before handle_text) instead of the image
    generation confirmation."""
    from aiogram.fsm.storage.base import StorageKey

    key = StorageKey(chat_id=2001, user_id=1001, bot_id=bot.bot.id)
    storage = bot.dp.storage
    await storage.set_state(
        key,
        variables_flow._state_key(variables_flow.VarStates.add_item),
    )
    await storage.set_data(key, {"vars_list": "poses", "vars_message_id": 5, "vars_chat_id": 2001})
    try:
        with patch.object(bot, "ALLOWED_TELEGRAM_IDS", None):
            with patch.dict(
                bot._VARS_DEPS,
                {"allowed_telegram_ids": None, "variables_admin_ids": None},
            ):
                with patch.object(bot.bot, "session", new_callable=AsyncMock) as mock_session:
                    await bot.dp.feed_update(
                        bot.bot,
                        _real_update(text="volando"),
                    )
        # item added, refreshed list panel sent, no generation confirmation
        assert "volando" in variables_store.get_list("poses")
        sent_texts = []
        for call in mock_session.call_args_list:
            method = call.args[1]
            if type(method).__name__ == "SendMessage":
                sent_texts.append(method.text or "")
        assert any("volando" in t for t in sent_texts)
        assert not any("Confirmar" in t for t in sent_texts)
    finally:
        await storage.set_state(key, None)
