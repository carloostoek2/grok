#!/usr/bin/env python3
"""Telegram admin panel for the /variables editing lists (CRUD).

Manage the persistent lists of poses, angles, and actions used by /variables,
plus the prompt template. All state is stored via variables_store (JSON).

Flow:
    /listas              → main menu (list counts + template + list buttons)
    list screen          → items shown numbered, with Añadir / Editar / Eliminar
    Añadir               → type the new item text
    Editar               → pick an item, then type the replacement text
    Eliminar             → pick an item to remove
    Plantilla            → type the new template with {pose}/{angle}/{action}

Callback data layout:
    var:open:<list>          open a list screen
    var:add:<list>           request new item text
    var:edit:<list>          show item picker for editing
    var:del:<list>           show item picker for deletion
    var:item:edit:<list>:<i> pick item <i> to edit
    var:item:del:<list>:<i>  pick item <i> to delete
    var:tmpl                 edit the prompt template
    var:back                 back to main menu
    var:close                close the panel
"""

from __future__ import annotations

import html
from typing import Any

from aiogram import Dispatcher, F, types
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import variables_store

LIST_LABELS = {
    "poses": "Poses",
    "angles": "Ángulos",
    "actions": "Acciones",
}

# Telegram allows up to 100 inline buttons; keep headroom for the back button.
PICKER_MAX_ITEMS = 90
# Items rendered in the list screen (Telegram text limit ~4096 chars).
LIST_DISPLAY_MAX = 30

_NON_PRIVATE_CHAT_TYPES = frozenset({"group", "supergroup", "channel"})


class VarStates(StatesGroup):
    menu = State()
    add_item = State()
    edit_text = State()
    template = State()


def _chat_is_private(chat: types.Chat) -> bool:
    chat_type = getattr(chat, "type", None)
    if chat_type is None:
        return True
    if hasattr(chat_type, "value"):
        chat_type = chat_type.value
    return str(chat_type) not in _NON_PRIVATE_CHAT_TYPES


async def _reject_non_private_message(message: types.Message) -> bool:
    """Return True when the command was rejected (non-private chat or no admin rights)."""
    if not _chat_is_private(message.chat):
        await message.answer("La administración de variables solo está disponible en chats privados.")
        return True
    if not _is_admin(message.from_user.id):
        await message.answer("No tienes permiso para administrar las variables.")
        return True
    return False


async def _reject_non_private_callback(callback: types.CallbackQuery) -> bool:
    """Return True when the callback was rejected (non-private chat or no admin rights)."""
    if not _chat_is_private(callback.message.chat):
        await callback.answer(
            "La administración de variables solo está disponible en chats privados.",
            show_alert=True,
        )
        return True
    if not _is_admin(callback.from_user.id):
        await callback.answer(
            "No tienes permiso para administrar las variables.",
            show_alert=True,
        )
        return True
    return False


def _state_key(st: State) -> str:
    """Return the FSM state id as stored by aiogram (e.g. VarStates:menu)."""
    return st.state


async def _reject_stale_callback(
    callback: types.CallbackQuery,
    state: FSMContext,
    *,
    allowed_states: tuple[State, ...],
) -> bool:
    """Return True when the callback is stale and was rejected."""
    data = await state.get_data()
    stored_id = data.get("vars_message_id")
    if stored_id is not None and callback.message.message_id != stored_id:
        await callback.answer(
            "Esta pantalla ya no está activa. Usa /listas para empezar de nuevo.",
            show_alert=True,
        )
        return True
    current = await state.get_state()
    allowed = {_state_key(s) for s in allowed_states}
    if current not in allowed:
        await callback.answer(
            "Esta pantalla ya no está activa. Usa /listas para empezar de nuevo.",
            show_alert=True,
        )
        return True
    return False


def _deps() -> dict[str, Any]:
    global _VARS_DEPS
    return _VARS_DEPS


def _is_admin(user_id: int) -> bool:
    """Panel authorization: explicit VARIABLES_ADMIN_IDS wins; otherwise fall
    back to the bot allowlist; when neither is configured, everyone is admin
    (matching the bot's open default)."""
    deps = _deps()
    admins = deps.get("variables_admin_ids")
    if admins is not None:
        return user_id in admins
    allowed = deps.get("allowed_telegram_ids")
    if allowed is not None:
        return user_id in allowed
    return True


def _truncate(text: str, limit: int) -> str:
    text = text.replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _esc(text: str) -> str:
    return html.escape(text)


def _menu_text() -> str:
    lists = variables_store.get_lists()
    lines = [
        "<b>🎛 Listas de variables</b>\n",
        "Se usan en <b>/variables N</b> para editar imágenes combinando "
        "una opción aleatoria de cada lista.\n",
    ]
    for name in variables_store.LIST_NAMES:
        count = len(lists[name])
        label = LIST_LABELS[name]
        lines.append(f"• <b>{label}</b>: {count} opcione{'s' if count != 1 else 'n'}")
    template = variables_store.get_template()
    lines.append(f"\n<b>Plantilla:</b> <code>{_esc(_truncate(template, 80))}</code>")
    lines.append(
        "\n<i>Placeholders: {pose}, {angle}, {action}.</i> "
        "Toque una lista para gestionarla."
    )
    return "\n".join(lines)


def _menu_keyboard() -> InlineKeyboardMarkup:
    lists = variables_store.get_lists()
    buttons = []
    for name in variables_store.LIST_NAMES:
        label = LIST_LABELS[name]
        count = len(lists[name])
        buttons.append([
            InlineKeyboardButton(
                text=f"{label} ({count})",
                callback_data=f"var:open:{name}",
            )
        ])
    buttons.append([
        InlineKeyboardButton(text="✏️ Plantilla", callback_data="var:tmpl"),
        InlineKeyboardButton(text="❌ Cerrar", callback_data="var:close"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _list_text(name: str) -> str:
    items = variables_store.get_list(name)
    label = LIST_LABELS[name]
    lines = [f"<b>📌 {label}</b> — {len(items)} opcione{'s' if len(items) != 1 else 'n'}\n"]
    if not items:
        lines.append("<i>La lista está vacía.</i>")
    # Cap the rendered list so the message stays under Telegram's text limit
    # (items have no length limit; display is truncated per item).
    shown = items[:LIST_DISPLAY_MAX]
    for i, item in enumerate(shown, 1):
        lines.append(f"{i}. {_esc(_truncate(item, 80))}")
    hidden = len(items) - len(shown)
    if hidden > 0:
        lines.append(f"\n<i>… y {hidden} más (usa Editar/Eliminar para verlas).</i>")
    return "\n".join(lines)


def _list_keyboard(name: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ Añadir", callback_data=f"var:add:{name}"),
            InlineKeyboardButton(text="✏️ Editar", callback_data=f"var:edit:{name}"),
        ],
        [
            InlineKeyboardButton(text="🗑 Eliminar", callback_data=f"var:del:{name}"),
            InlineKeyboardButton(text="← Menú", callback_data="var:back"),
        ],
        [InlineKeyboardButton(text="❌ Cerrar", callback_data="var:close")],
    ])


def _item_picker_keyboard(name: str, items: list[str], action: str) -> InlineKeyboardMarkup:
    rows = []
    for i, item in enumerate(items[:PICKER_MAX_ITEMS]):
        rows.append([
            InlineKeyboardButton(
                text=f"{i + 1}. {_truncate(item, 40)}",
                callback_data=f"var:item:{action}:{name}:{i}",
            )
        ])
    rows.append([InlineKeyboardButton(text="← Volver", callback_data=f"var:open:{name}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _validate_item_text(text: str) -> str | None:
    # No maximum length: list options can be long descriptive prompts.
    # Display truncation happens at render time (list screen / picker).
    if len(text) < 2:
        return "El texto es demasiado corto (mínimo 2 caracteres)."
    return None


def _validate_template_text(text: str) -> str | None:
    # No maximum length (display is truncated at render time in the menu).
    if len(text) < 3:
        return "La plantilla es demasiado corta."
    return None


async def _show_menu(target: types.Message, state: FSMContext, user_id: int) -> None:
    safe_edit_text = _deps()["safe_edit_text"]
    await state.set_state(VarStates.menu)
    await state.update_data(
        vars_message_id=target.message_id,
        vars_chat_id=target.chat.id,
    )
    await safe_edit_text(target, _menu_text(), parse_mode="HTML", reply_markup=_menu_keyboard())


async def _show_list(target: types.Message, state: FSMContext, user_id: int, name: str) -> None:
    safe_edit_text = _deps()["safe_edit_text"]
    await state.set_state(VarStates.menu)
    await state.update_data(
        vars_message_id=target.message_id,
        vars_chat_id=target.chat.id,
    )
    await safe_edit_text(
        target,
        _list_text(name),
        parse_mode="HTML",
        reply_markup=_list_keyboard(name),
    )


async def _show_new_panel(
    message: types.Message,
    state: FSMContext,
    user_id: int,
    text: str,
    keyboard: InlineKeyboardMarkup,
) -> None:
    """Refresh the panel after a text input.

    Bots cannot edit user-sent messages, so the new state is sent as a fresh
    bot message and the previous bot panel (whose id is stored in FSM state)
    is deleted best-effort.
    """
    data = await state.get_data()
    old_chat_id = data.get("vars_chat_id")
    old_message_id = data.get("vars_message_id")
    new_msg = await message.answer(text, parse_mode="HTML", reply_markup=keyboard)
    await state.set_state(VarStates.menu)
    await state.update_data(
        vars_message_id=new_msg.message_id,
        vars_chat_id=new_msg.chat.id,
    )
    if old_chat_id and old_message_id:
        try:
            await message.bot.delete_message(
                chat_id=old_chat_id,
                message_id=old_message_id,
            )
        except Exception:
            # Best-effort cleanup; the stale guard makes old buttons inert anyway.
            pass


# ---------------------------------------------------------------------------
# /listas
# ---------------------------------------------------------------------------
async def cmd_listas(message: types.Message, state: FSMContext):
    if await _reject_non_private_message(message):
        return
    await state.clear()
    # Establish the panel state so the very first button tap is not rejected
    # as stale (the stale guard requires VarStates.menu + matching message id).
    await state.set_state(VarStates.menu)
    sent = await message.answer(_menu_text(), parse_mode="HTML", reply_markup=_menu_keyboard())
    await state.update_data(
        vars_message_id=sent.message_id,
        vars_chat_id=sent.chat.id,
    )


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------
async def handle_var_open(callback: types.CallbackQuery, state: FSMContext):
    if await _reject_non_private_callback(callback):
        return
    if await _reject_stale_callback(callback, state, allowed_states=(VarStates.menu,)):
        return
    name = callback.data.split(":", 2)[2]
    if not variables_store.is_valid_list_name(name):
        await callback.answer("Lista no válida.", show_alert=True)
        return
    await _show_list(callback.message, state, callback.from_user.id, name)
    await callback.answer()


async def handle_var_back(callback: types.CallbackQuery, state: FSMContext):
    if await _reject_non_private_callback(callback):
        return
    if await _reject_stale_callback(callback, state, allowed_states=(VarStates.menu,)):
        return
    await _show_menu(callback.message, state, callback.from_user.id)
    await callback.answer()


async def handle_var_cancel(callback: types.CallbackQuery, state: FSMContext):
    """Cancel an in-progress add/edit/template input and return to the list/menu."""
    if await _reject_non_private_callback(callback):
        return
    current = await state.get_state()
    allowed = {
        _state_key(VarStates.add_item),
        _state_key(VarStates.edit_text),
        _state_key(VarStates.template),
    }
    if current not in allowed:
        # Stale cancel button (panel already closed/navigated) — ignore.
        await callback.answer()
        return
    data = await state.get_data()
    name = data.get("vars_list")
    if variables_store.is_valid_list_name(name):
        await _show_list(callback.message, state, callback.from_user.id, name)
    else:
        await _show_menu(callback.message, state, callback.from_user.id)
    await callback.answer()


async def handle_var_close(callback: types.CallbackQuery, state: FSMContext):
    if await _reject_non_private_callback(callback):
        return
    if await _reject_stale_callback(callback, state, allowed_states=(VarStates.menu,)):
        return
    safe_edit_text = _deps()["safe_edit_text"]
    await state.clear()
    await safe_edit_text(callback.message, "Panel de variables cerrado.", reply_markup=None)
    await callback.answer()


async def _begin_add(callback: types.CallbackQuery, state: FSMContext, name: str) -> None:
    safe_edit_text = _deps()["safe_edit_text"]
    label = LIST_LABELS[name]
    await state.set_state(VarStates.add_item)
    await state.update_data(
        vars_list=name,
        vars_message_id=callback.message.message_id,
        vars_chat_id=callback.message.chat.id,
    )
    await safe_edit_text(
        callback.message,
        f"➕ <b>Añadir a {label}</b>\n\nEnvía el nuevo elemento de la lista. "
        f"Un elemento por mensaje.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="← Cancelar", callback_data="var:cancel")]
        ]),
    )
    await callback.answer()


async def handle_var_add(callback: types.CallbackQuery, state: FSMContext):
    if await _reject_non_private_callback(callback):
        return
    if await _reject_stale_callback(callback, state, allowed_states=(VarStates.menu,)):
        return
    name = callback.data.split(":", 2)[2]
    if not variables_store.is_valid_list_name(name):
        await callback.answer("Lista no válida.", show_alert=True)
        return
    await _begin_add(callback, state, name)


async def _begin_picker(callback: types.CallbackQuery, state: FSMContext, name: str, action: str) -> None:
    safe_edit_text = _deps()["safe_edit_text"]
    items = variables_store.get_list(name)
    label = LIST_LABELS[name]
    if not items:
        await callback.answer("La lista está vacía. Añade elementos primero.", show_alert=True)
        return
    verb = "Editar" if action == "edit" else "Eliminar"
    await state.set_state(VarStates.menu)
    await state.update_data(
        vars_message_id=callback.message.message_id,
        vars_chat_id=callback.message.chat.id,
    )
    await safe_edit_text(
        callback.message,
        f"<b>{verb} {label}</b>\n\nToca el elemento:",
        parse_mode="HTML",
        reply_markup=_item_picker_keyboard(name, items, action),
    )
    await callback.answer()


async def handle_var_edit_list(callback: types.CallbackQuery, state: FSMContext):
    if await _reject_non_private_callback(callback):
        return
    if await _reject_stale_callback(callback, state, allowed_states=(VarStates.menu,)):
        return
    name = callback.data.split(":", 2)[2]
    if not variables_store.is_valid_list_name(name):
        await callback.answer("Lista no válida.", show_alert=True)
        return
    await _begin_picker(callback, state, name, "edit")


async def handle_var_del_list(callback: types.CallbackQuery, state: FSMContext):
    if await _reject_non_private_callback(callback):
        return
    if await _reject_stale_callback(callback, state, allowed_states=(VarStates.menu,)):
        return
    name = callback.data.split(":", 2)[2]
    if not variables_store.is_valid_list_name(name):
        await callback.answer("Lista no válida.", show_alert=True)
        return
    await _begin_picker(callback, state, name, "del")


async def handle_var_item_edit(callback: types.CallbackQuery, state: FSMContext):
    if await _reject_non_private_callback(callback):
        return
    if await _reject_stale_callback(callback, state, allowed_states=(VarStates.menu,)):
        return
    parts = callback.data.split(":")
    if len(parts) != 5 or parts[2] != "edit":
        await callback.answer("Acción no válida.", show_alert=True)
        return
    _, _, action, name, index_str = parts
    if not variables_store.is_valid_list_name(name):
        await callback.answer("Lista no válida.", show_alert=True)
        return
    try:
        index = int(index_str)
    except ValueError:
        await callback.answer("Elemento no válido.", show_alert=True)
        return
    items = variables_store.get_list(name)
    if not 0 <= index < len(items):
        await callback.answer("El elemento ya no existe.", show_alert=True)
        return
    safe_edit_text = _deps()["safe_edit_text"]
    label = LIST_LABELS[name]
    await state.set_state(VarStates.edit_text)
    await state.update_data(
        vars_list=name,
        vars_index=index,
        vars_message_id=callback.message.message_id,
        vars_chat_id=callback.message.chat.id,
    )
    await safe_edit_text(
        callback.message,
        f"✏️ <b>Editar {label}</b> — elemento {index + 1}\n\n"
        f"Actual: {_esc(_truncate(items[index], 120))}\n\n"
        f"Envía el nuevo texto:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="← Cancelar", callback_data="var:cancel")]
        ]),
    )
    await callback.answer()


async def handle_var_item_del(callback: types.CallbackQuery, state: FSMContext):
    if await _reject_non_private_callback(callback):
        return
    if await _reject_stale_callback(callback, state, allowed_states=(VarStates.menu,)):
        return
    parts = callback.data.split(":")
    if len(parts) != 5 or parts[2] != "del":
        await callback.answer("Acción no válida.", show_alert=True)
        return
    _, _, action, name, index_str = parts
    if not variables_store.is_valid_list_name(name):
        await callback.answer("Lista no válida.", show_alert=True)
        return
    try:
        index = int(index_str)
    except ValueError:
        await callback.answer("Elemento no válido.", show_alert=True)
        return
    items = variables_store.get_list(name)
    if not 0 <= index < len(items):
        await callback.answer("El elemento ya no existe.", show_alert=True)
        return
    removed = items[index]
    variables_store.delete_item(name, index)
    await _show_list(callback.message, state, callback.from_user.id, name)
    await callback.answer(f"Eliminado: {_truncate(removed, 40)}")


async def handle_var_tmpl(callback: types.CallbackQuery, state: FSMContext):
    if await _reject_non_private_callback(callback):
        return
    if await _reject_stale_callback(callback, state, allowed_states=(VarStates.menu,)):
        return
    safe_edit_text = _deps()["safe_edit_text"]
    template = variables_store.get_template()
    await state.set_state(VarStates.template)
    await state.update_data(
        vars_message_id=callback.message.message_id,
        vars_chat_id=callback.message.chat.id,
    )
    await safe_edit_text(
        callback.message,
        "✏️ <b>Plantilla del prompt</b>\n\n"
        "Usa los placeholders <code>{pose}</code>, <code>{angle}</code> y "
        "<code>{action}</code> para insertar las opciones aleatorias.\n\n"
        f"Actual: <code>{_esc(_truncate(template, 200))}</code>\n\n"
        "Envía la nueva plantilla:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="← Cancelar", callback_data="var:cancel")]
        ]),
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# Text input (FSM states)
# ---------------------------------------------------------------------------
async def handle_add_text(message: types.Message, state: FSMContext):
    if await _reject_non_private_message(message):
        return
    data = await state.get_data()
    name = data.get("vars_list")
    if not variables_store.is_valid_list_name(name):
        await state.clear()
        await message.answer("Sesión desactualizada. Usa /listas de nuevo.")
        return
    text = message.text.strip()
    err = _validate_item_text(text)
    if err:
        await message.answer(err)
        return
    if not variables_store.add_item(name, text):
        await message.answer("Ese elemento ya existe en la lista.")
        return
    await _show_new_panel(
        message,
        state,
        message.from_user.id,
        _list_text(name),
        _list_keyboard(name),
    )


async def handle_edit_text(message: types.Message, state: FSMContext):
    if await _reject_non_private_message(message):
        return
    data = await state.get_data()
    name = data.get("vars_list")
    index = data.get("vars_index")
    if not variables_store.is_valid_list_name(name) or not isinstance(index, int):
        await state.clear()
        await message.answer("Sesión desactualizada. Usa /listas de nuevo.")
        return
    text = message.text.strip()
    err = _validate_item_text(text)
    if err:
        await message.answer(err)
        return
    if not variables_store.update_item(name, index, text):
        await message.answer("No se pudo editar (¿elemento eliminado o duplicado?).")
        return
    await _show_new_panel(
        message,
        state,
        message.from_user.id,
        _list_text(name),
        _list_keyboard(name),
    )


async def handle_template_text(message: types.Message, state: FSMContext):
    if await _reject_non_private_message(message):
        return
    text = message.text.strip()
    err = _validate_template_text(text)
    if err:
        await message.answer(err)
        return
    if not variables_store.set_template(text):
        await message.answer("No se pudo guardar la plantilla.")
        return
    await _show_new_panel(
        message,
        state,
        message.from_user.id,
        _menu_text(),
        _menu_keyboard(),
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
_VARS_DEPS: dict[str, Any] = {}


def register_variables_handlers(dp: Dispatcher, deps: dict[str, Any]) -> None:
    """Register the /listas admin panel command and its callbacks/text handlers."""
    global _VARS_DEPS
    _VARS_DEPS = deps

    dp.message.register(cmd_listas, Command("listas"))
    dp.message.register(handle_add_text, StateFilter(VarStates.add_item), F.text)
    dp.message.register(handle_edit_text, StateFilter(VarStates.edit_text), F.text)
    dp.message.register(handle_template_text, StateFilter(VarStates.template), F.text)
    dp.callback_query.register(handle_var_open, lambda c: c.data and c.data.startswith("var:open:"))
    dp.callback_query.register(handle_var_add, lambda c: c.data and c.data.startswith("var:add:"))
    dp.callback_query.register(handle_var_edit_list, lambda c: c.data and c.data.startswith("var:edit:"))
    dp.callback_query.register(handle_var_del_list, lambda c: c.data and c.data.startswith("var:del:"))
    dp.callback_query.register(handle_var_item_edit, lambda c: c.data and c.data.startswith("var:item:edit:"))
    dp.callback_query.register(handle_var_item_del, lambda c: c.data and c.data.startswith("var:item:del:"))
    dp.callback_query.register(handle_var_tmpl, lambda c: c.data == "var:tmpl")
    dp.callback_query.register(handle_var_cancel, lambda c: c.data == "var:cancel")
    dp.callback_query.register(handle_var_back, lambda c: c.data == "var:back")
    dp.callback_query.register(handle_var_close, lambda c: c.data == "var:close")
