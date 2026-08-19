"""ComfyUI refine-confirm flow (item 2): two-stage generation with interactive
confirmation. Covers base-only _generate_comfyui (meta with comfyui_remotes),
_generate_comfyui_refine (REFINE_ONLY=1, validated paths, scaled timeout),
the _send_comfyui_output choke -> _send_comfyui_confirm_refine decision flow,
handle_refine_decision idempotency, cancel force-resolve and _finish_job cleanup.

pytest.ini sets asyncio_mode=auto, so async tests need no marker.
"""

from __future__ import annotations

import asyncio
import subprocess
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bot
import sessions


def _status_message():
    msg = MagicMock()
    msg.edit_text = AsyncMock()
    msg.delete = AsyncMock()
    msg.text = "status"
    msg.caption = None
    return msg


def _comfyui_model(**over):
    return {
        "provider": "comfyui",
        "comfyui_model": "krea2",
        "comfyui_lora": "none",
        "comfyui_refine": "1",
        **over,
    }


def _regen_ctx(uid):
    return {"model_key": "comfyui", "user_id": uid, "prompt": "prompt", "mode": "edit"}


def _message(uid, msg_id, chat_id=9001):
    msg = MagicMock()
    msg.from_user.id = uid
    msg.message_id = msg_id
    msg.chat.id = chat_id
    return msg


def _refine_callback(uid, data):
    cb = MagicMock()
    cb.from_user.id = uid
    cb.data = data
    cb.answer = AsyncMock()
    cb.message = MagicMock()
    return cb


# --- _generate_comfyui base-only + meta ------------------------------------


async def test_generate_comfyui_base_only_no_refine_env(tmp_path):
    local = tmp_path / "out.png"
    with patch.object(bot, "_comfyui_ssh_base", return_value=("ssh -p 22 root@box", 22, None)):
        with patch.object(
            bot,
            "_comfyui_run_remote",
            new_callable=AsyncMock,
            return_value=(["/workspace/ComfyUI/output/base_a.png"], 0),
        ) as run_remote:
            with patch.object(bot, "_comfyui_pull", new_callable=AsyncMock, return_value=str(local)):
                locals_, err, meta = await bot._generate_comfyui(_comfyui_model(), "a cat")

    assert err is None
    assert locals_ == [str(local)]
    assert meta == {"comfyui_remotes": ["/workspace/ComfyUI/output/base_a.png"]}
    cmd = run_remote.await_args.args[0]
    assert "REFINE=" not in cmd
    assert "MODEL='krea2'" in cmd


# --- _generate_comfyui_refine + path validation -----------------------------


async def test_generate_comfyui_refine_runs_refine_only(tmp_path):
    local = tmp_path / "refined.png"
    with patch.object(bot, "_comfyui_ssh_base", return_value=("ssh -p 22 root@box", 22, None)):
        with patch.object(
            bot,
            "_comfyui_run_remote",
            new_callable=AsyncMock,
            return_value=(["/workspace/ComfyUI/output/refined_base_a.png"], 0),
        ) as run_remote:
            with patch.object(bot, "_comfyui_pull", new_callable=AsyncMock, return_value=str(local)):
                locals_, err = await bot._generate_comfyui_refine(
                    _comfyui_model(),
                    "make it prettier",
                    ["/workspace/ComfyUI/output/base_a.png"],
                )

    assert err is None
    assert locals_ == [str(local)]
    cmd = run_remote.await_args.args[0]
    assert "REFINE_ONLY='1'" in cmd
    assert "REFINE_INPUT='/workspace/ComfyUI/output/base_a.png'" in cmd
    assert "INPUT_IMAGE=" not in cmd
    assert run_remote.await_args.kwargs["timeout"] >= 1200


async def test_generate_comfyui_refine_rejects_invalid_paths():
    with patch.object(bot, "_comfyui_ssh_base", return_value=("ssh -p 22 root@box", 22, None)):
        with patch.object(bot, "_comfyui_run_remote", new_callable=AsyncMock) as run_remote:
            locals_, err = await bot._generate_comfyui_refine(
                _comfyui_model(),
                "p",
                ["/workspace/ok.png", "/workspace/bad;x.png"],
            )

    assert err is not None
    assert locals_ is None
    run_remote.assert_not_called()


async def test_refine_remote_path_validation():
    assert bot._validate_refine_remote_path("/workspace/ComfyUI/output/refined_a.png") is True
    for bad in [
        "/workspace/a b.png",
        "/workspace/a;rm -rf x.png",
        "/workspace/a`id`.png",
        "/workspace/a$(id).png",
        "/tmp/outside.png",
        "'",
        "/workspace/a\nb.png",
    ]:
        assert bot._validate_refine_remote_path(bad) is False


def test_refine_confirm_keyboard_callback_data():
    kb = bot._refine_confirm_keyboard("tok")
    assert [b.callback_data for b in kb.inline_keyboard[0]] == [
        "refine:tok:yes",
        "refine:tok:no",
    ]


async def test_generate_comfyui_refine_multi_base_timeout_scales():
    paths = [f"/workspace/ComfyUI/output/base_{i}.png" for i in range(3)]
    with patch.object(bot, "_comfyui_ssh_base", return_value=("ssh -p 22 root@box", 22, None)):
        with patch.object(
            bot, "_comfyui_run_remote", new_callable=AsyncMock,
            return_value=(["/workspace/ComfyUI/output/refined_0.png"], 0),
        ) as run_remote:
            with patch.object(bot, "_comfyui_pull", new_callable=AsyncMock, return_value="/tmp/r.png"):
                locals_, err = await bot._generate_comfyui_refine(_comfyui_model(), "p", paths)

    assert err is None
    assert locals_ == ["/tmp/r.png"]
    assert run_remote.await_args.kwargs["timeout"] == 1200 * 3 + 300
    cmd = run_remote.await_args.args[0]
    assert "REFINE_INPUT='/workspace/ComfyUI/output/base_0.png," in cmd
    assert (
        "'/workspace/ComfyUI/output/base_0.png,/workspace/ComfyUI/output/base_1.png,"
        "/workspace/ComfyUI/output/base_2.png'" in cmd
    )


async def test_generate_comfyui_refine_empty_remote_paths():
    with patch.object(bot, "_comfyui_ssh_base", return_value=("ssh -p 22 root@box", 22, None)):
        with patch.object(bot, "_comfyui_run_remote", new_callable=AsyncMock) as run_remote:
            locals_, err = await bot._generate_comfyui_refine(_comfyui_model(), "p", [])

    assert err is not None
    assert locals_ is None
    run_remote.assert_not_called()


async def test_comfyui_run_remote_timeout_expired_returns_empty():
    with patch.object(bot, "_comfyui_ssh_base", return_value=("ssh -p 22 root@box", 22, None)):
        with patch.object(
            asyncio,
            "to_thread",
            new=AsyncMock(side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=10)),
        ):
            lines, rc = await bot._comfyui_run_remote("cmd", "prompt", timeout=60)

    assert lines == []
    assert rc is None


async def test_comfyui_run_remote_maps_returncode():
    with patch.object(bot, "_comfyui_ssh_base", return_value=("ssh -p 22 root@box", 22, None)):
        with patch.object(
            asyncio, "to_thread",
            new=AsyncMock(return_value=MagicMock(returncode=2, stdout=b"", stderr=b"")),
        ):
            lines, rc = await bot._comfyui_run_remote("cmd", "prompt")

    assert lines == []
    assert rc == 2


async def test_generate_comfyui_refine_maps_exit_codes():
    for rc, expected in [
        (2, "Configuración de refino inválida"),
        (3, "El refino no produjo imágenes"),
        (9, "no devolvió imagen"),
    ]:
        with patch.object(bot, "_comfyui_ssh_base", return_value=("ssh -p 22 root@box", 22, None)):
            with patch.object(
                bot, "_comfyui_run_remote", new_callable=AsyncMock,
                return_value=([], rc),
            ):
                _locals, err = await bot._generate_comfyui_refine(
                    _comfyui_model(), "p", ["/workspace/ComfyUI/output/base_a.png"]
                )
        assert err is not None and expected in err


# --- _send_comfyui_output choke -> _send_comfyui_confirm_refine -------------


async def test_send_comfyui_output_confirm_yes_single():
    bot._pending_refine.clear()
    uid = 6001
    message = _message(uid, 11)
    status_msg = _status_message()
    event = asyncio.Event()
    event.job_id = "job1"

    base_msg = MagicMock()
    base_msg.delete = AsyncMock()
    base_msg.edit_reply_markup = AsyncMock()
    refined_msg = MagicMock()
    refined_msg.delete = AsyncMock()
    refined_msg.edit_reply_markup = AsyncMock()

    refine_mock = AsyncMock(return_value=(["/tmp/refined.png"], None))

    with patch.object(
        bot, "_send_comfyui_image", new_callable=AsyncMock, side_effect=[base_msg, refined_msg]
    ) as send_img:
        with patch.object(bot, "_generate_comfyui_refine", new=refine_mock):
            task = asyncio.create_task(
                bot._send_comfyui_output(
                    _comfyui_model(),
                    "/tmp/base.png",
                    "prompt",
                    status_msg,
                    message,
                    "Edit",
                    _regen_ctx(uid),
                    meta={"comfyui_remotes": ["/workspace/ComfyUI/output/base_a.png"]},
                    cancel_event=event,
                )
            )
            for _ in range(100):
                if bot._pending_refine:
                    break
                await asyncio.sleep(0)
            assert bot._pending_refine, "pending refine never registered"
            token = next(iter(bot._pending_refine))
            cb = _refine_callback(uid, f"refine:{token}:yes")
            await bot.handle_refine_decision(cb)
            result = await asyncio.wait_for(task, timeout=5)

    assert result is True
    refine_mock.assert_awaited_once()
    assert refine_mock.await_args.args[2] == ["/workspace/ComfyUI/output/base_a.png"]
    assert send_img.await_count == 2
    # base send: must carry the confirm keyboard, save the ref, keep the status.
    base_call = send_img.await_args_list[0]
    assert base_call.args[0] == "/tmp/base.png"
    assert base_call.kwargs.get("reply_markup") == bot._refine_confirm_keyboard(token)
    assert base_call.kwargs.get("save_ref") is True
    assert base_call.kwargs.get("delete_status") is False
    # refined send
    refined_call = send_img.await_args_list[1]
    assert refined_call.args[0] == "/tmp/refined.png"
    assert refined_call.kwargs.get("delete_status") is True
    base_msg.delete.assert_awaited_once()


async def test_send_comfyui_output_confirm_no_keeps_base():
    bot._pending_refine.clear()
    uid = 6002
    message = _message(uid, 21)
    status_msg = _status_message()
    event = asyncio.Event()
    event.job_id = "job2"

    base_msg = MagicMock()
    base_msg.delete = AsyncMock()
    base_msg.edit_reply_markup = AsyncMock()

    refine_mock = AsyncMock()

    with patch.object(
        bot, "_send_comfyui_image", new_callable=AsyncMock, side_effect=[base_msg]
    ) as send_img:
        with patch.object(bot, "_generate_comfyui_refine", new=refine_mock):
            task = asyncio.create_task(
                bot._send_comfyui_output(
                    _comfyui_model(),
                    "/tmp/base.png",
                    "prompt",
                    status_msg,
                    message,
                    "Edit",
                    _regen_ctx(uid),
                    meta={"comfyui_remotes": ["/workspace/ComfyUI/output/base_a.png"]},
                    cancel_event=event,
                )
            )
            for _ in range(100):
                if bot._pending_refine:
                    break
                await asyncio.sleep(0)
            assert bot._pending_refine, "pending refine never registered"
            token = next(iter(bot._pending_refine))
            cb = _refine_callback(uid, f"refine:{token}:no")
            await bot.handle_refine_decision(cb)
            result = await asyncio.wait_for(task, timeout=5)

    assert result is True
    refine_mock.assert_not_awaited()
    assert send_img.await_count == 1
    base_msg.edit_reply_markup.assert_awaited_once()
    kb = base_msg.edit_reply_markup.await_args.kwargs.get("reply_markup")
    assert kb == bot._image_regenerate_keyboard()
    status_msg.delete.assert_awaited_once()


async def test_send_comfyui_output_confirm_timeout_finalizes(monkeypatch):
    bot._pending_refine.clear()
    monkeypatch.setattr(bot, "REFINE_CONFIRM_TIMEOUT", 0)
    uid = 6003
    message = _message(uid, 31)
    status_msg = _status_message()
    event = asyncio.Event()
    event.job_id = "job3"

    base_msg = MagicMock()
    base_msg.delete = AsyncMock()
    base_msg.edit_reply_markup = AsyncMock()

    refine_mock = AsyncMock()

    with patch.object(
        bot, "_send_comfyui_image", new_callable=AsyncMock, side_effect=[base_msg]
    ) as send_img:
        with patch.object(bot, "_generate_comfyui_refine", new=refine_mock):
            task = asyncio.create_task(
                bot._send_comfyui_output(
                    _comfyui_model(),
                    "/tmp/base.png",
                    "prompt",
                    status_msg,
                    message,
                    "Edit",
                    _regen_ctx(uid),
                    meta={"comfyui_remotes": ["/workspace/ComfyUI/output/base_a.png"]},
                    cancel_event=event,
                )
            )
            result = await asyncio.wait_for(task, timeout=5)

    assert result is True
    refine_mock.assert_not_awaited()
    assert send_img.await_count == 1
    base_msg.edit_reply_markup.assert_awaited_once()
    kb = base_msg.edit_reply_markup.await_args.kwargs.get("reply_markup")
    assert kb == bot._image_regenerate_keyboard()


async def test_send_comfyui_output_confirm_yes_album():
    bot._pending_refine.clear()
    uid = 6004
    message = _message(uid, 41)
    confirm_msg = MagicMock()
    confirm_msg.delete = AsyncMock()
    confirm_msg.edit_text = AsyncMock()
    message.answer = AsyncMock(return_value=confirm_msg)
    status_msg = _status_message()
    event = asyncio.Event()
    event.job_id = "job4"

    base_msgs = [MagicMock(), MagicMock()]
    for m in base_msgs:
        m.delete = AsyncMock()
    refined_msgs = [MagicMock(), MagicMock()]
    for m in refined_msgs:
        m.delete = AsyncMock()

    refine_mock = AsyncMock(return_value=(["/tmp/r1.png", "/tmp/r2.png"], None))

    with patch.object(
        bot, "_send_comfyui_album", new_callable=AsyncMock, side_effect=[base_msgs, refined_msgs]
    ) as send_album:
        with patch.object(bot, "_generate_comfyui_refine", new=refine_mock):
            task = asyncio.create_task(
                bot._send_comfyui_output(
                    _comfyui_model(),
                    ["/tmp/a.png", "/tmp/b.png"],
                    "prompt",
                    status_msg,
                    message,
                    "Edit",
                    _regen_ctx(uid),
                    meta={"comfyui_remotes": ["/workspace/ComfyUI/output/base_a.png"]},
                    cancel_event=event,
                )
            )
            for _ in range(100):
                if bot._pending_refine:
                    break
                await asyncio.sleep(0)
            assert bot._pending_refine, "pending refine never registered"
            token = next(iter(bot._pending_refine))
            cb = _refine_callback(uid, f"refine:{token}:yes")
            await bot.handle_refine_decision(cb)
            result = await asyncio.wait_for(task, timeout=5)

    assert result is True
    assert send_album.await_count == 2
    # base album: saved as the ref, status kept, and the confirm prompt carries
    # the confirm keyboard.
    base_call = send_album.await_args_list[0]
    assert base_call.args[0] == ["/tmp/a.png", "/tmp/b.png"]
    assert base_call.kwargs.get("save_ref") is True
    assert base_call.kwargs.get("delete_status") is False
    assert message.answer.await_args.kwargs.get("reply_markup") == bot._refine_confirm_keyboard(token)
    refined_call = send_album.await_args_list[1]
    assert refined_call.args[0] == ["/tmp/r1.png", "/tmp/r2.png"]
    assert refined_call.kwargs.get("delete_status") is True
    confirm_msg.delete.assert_awaited_once()
    for m in base_msgs:
        m.delete.assert_not_awaited()


async def test_generate_once_comfyui_propagates_meta():
    meta = {"comfyui_remotes": ["/workspace/ComfyUI/output/base_a.png"]}
    with patch.object(
        bot, "_generate_comfyui", new_callable=AsyncMock,
        return_value=(["/tmp/base.png"], None, meta),
    ):
        output, err, got_meta = await bot._generate_once(_comfyui_model(), "a cat")

    assert err is None
    assert output == ["/tmp/base.png"]
    assert got_meta == meta


async def test_send_comfyui_output_video_bypasses_refine_choke():
    uid = 6007
    message = _message(uid, 71)
    status_msg = _status_message()
    model = _comfyui_model(comfyui_model="wan_i2v", comfyui_refine="1")
    with patch.object(bot, "_send_comfyui_video", new_callable=AsyncMock, return_value=True) as send_video:
        with patch.object(bot, "_send_comfyui_confirm_refine", new_callable=AsyncMock) as confirm:
            with patch.object(bot, "_send_comfyui_image", new_callable=AsyncMock) as send_img:
                ok = await bot._send_comfyui_output(
                    model, "/tmp/v.mp4", "p", status_msg, message, "Edit",
                    _regen_ctx(uid),
                    meta={"comfyui_remotes": ["/workspace/ComfyUI/output/base_a.png"]},
                )

    assert ok is True
    send_video.assert_awaited_once()
    confirm.assert_not_awaited()
    send_img.assert_not_awaited()


async def test_send_comfyui_output_confirm_single_base_send_failure():
    bot._pending_refine.clear()
    uid = 6008
    message = _message(uid, 81)
    status_msg = _status_message()
    event = asyncio.Event()
    event.job_id = "job8"

    with patch.object(bot, "_send_comfyui_image", new_callable=AsyncMock, return_value=None) as send_img:
        result = await bot._send_comfyui_output(
            _comfyui_model(),
            "/tmp/base.png",
            "prompt",
            status_msg,
            message,
            "Edit",
            _regen_ctx(uid),
            meta={"comfyui_remotes": ["/workspace/ComfyUI/output/base_a.png"]},
            cancel_event=event,
        )

    assert result is False
    assert not bot._pending_refine
    send_img.assert_awaited_once()


async def test_send_comfyui_output_confirm_album_base_send_failure():
    bot._pending_refine.clear()
    uid = 6009
    message = _message(uid, 82)
    status_msg = _status_message()

    with patch.object(bot, "_send_comfyui_album", new_callable=AsyncMock, return_value=None):
        result = await bot._send_comfyui_output(
            _comfyui_model(),
            ["/tmp/a.png", "/tmp/b.png"],
            "prompt",
            status_msg,
            message,
            "Edit",
            _regen_ctx(uid),
            meta={"comfyui_remotes": ["/workspace/ComfyUI/output/base_a.png"]},
        )

    assert result is False
    assert not bot._pending_refine


async def test_send_comfyui_output_confirm_album_refined_send_failure():
    bot._pending_refine.clear()
    uid = 6011
    message = _message(uid, 83)
    confirm_msg = MagicMock()
    confirm_msg.delete = AsyncMock()
    confirm_msg.edit_text = AsyncMock()
    message.answer = AsyncMock(return_value=confirm_msg)
    status_msg = _status_message()
    event = asyncio.Event()
    event.job_id = "job11"

    refine_mock = AsyncMock(return_value=(["/tmp/r1.png", "/tmp/r2.png"], None))

    with patch.object(
        bot, "_send_comfyui_album", new_callable=AsyncMock, side_effect=[[MagicMock(), MagicMock()], None]
    ) as send_album:
        with patch.object(bot, "_generate_comfyui_refine", new=refine_mock):
            task = asyncio.create_task(
                bot._send_comfyui_output(
                    _comfyui_model(),
                    ["/tmp/a.png", "/tmp/b.png"],
                    "prompt",
                    status_msg,
                    message,
                    "Edit",
                    _regen_ctx(uid),
                    meta={"comfyui_remotes": ["/workspace/ComfyUI/output/base_a.png"]},
                    cancel_event=event,
                )
            )
            for _ in range(100):
                if bot._pending_refine:
                    break
                await asyncio.sleep(0)
            token = next(iter(bot._pending_refine))
            cb = _refine_callback(uid, f"refine:{token}:yes")
            await bot.handle_refine_decision(cb)
            result = await asyncio.wait_for(task, timeout=5)

    assert result is False
    assert send_album.await_count == 2
    assert "refinadas" in status_msg.edit_text.await_args.args[0]
    # the dangling "Refinando…" confirm message is cleaned up (R2-4)
    confirm_msg.delete.assert_awaited_once()


async def test_send_comfyui_output_confirm_single_refined_send_failure():
    # R2-3: _generate_comfyui_refine succeeds but the refined _send_comfyui_image
    # returns None -> error surfaced, base kept with its regen keyboard, and a
    # truthful (False) return.
    bot._pending_refine.clear()
    uid = 6012
    message = _message(uid, 84)
    status_msg = _status_message()
    event = asyncio.Event()
    event.job_id = "job12"

    base_msg = MagicMock()
    base_msg.delete = AsyncMock()
    base_msg.edit_reply_markup = AsyncMock()

    refine_mock = AsyncMock(return_value=(["/tmp/refined.png"], None))

    with patch.object(
        bot, "_send_comfyui_image", new_callable=AsyncMock, side_effect=[base_msg, None]
    ) as send_img:
        with patch.object(bot, "_generate_comfyui_refine", new=refine_mock):
            task = asyncio.create_task(
                bot._send_comfyui_output(
                    _comfyui_model(),
                    "/tmp/base.png",
                    "prompt",
                    status_msg,
                    message,
                    "Edit",
                    _regen_ctx(uid),
                    meta={"comfyui_remotes": ["/workspace/ComfyUI/output/base_a.png"]},
                    cancel_event=event,
                )
            )
            for _ in range(100):
                if bot._pending_refine:
                    break
                await asyncio.sleep(0)
            assert bot._pending_refine, "pending refine never registered"
            token = next(iter(bot._pending_refine))
            cb = _refine_callback(uid, f"refine:{token}:yes")
            await bot.handle_refine_decision(cb)
            result = await asyncio.wait_for(task, timeout=5)

    assert result is False
    refine_mock.assert_awaited_once()
    assert send_img.await_count == 2
    # error surfaced on the status message (last edit)
    assert "refinada" in status_msg.edit_text.await_args.args[0]
    # base kept and restored to its final (regen) keyboard, not deleted
    base_msg.delete.assert_not_awaited()
    kb = base_msg.edit_reply_markup.await_args.kwargs.get("reply_markup")
    assert kb == bot._image_regenerate_keyboard()


async def test_send_comfyui_output_confirm_yes_cancel_during_refine_keeps_base():
    # R2-1 regression for B2: a cancel that fires DURING the refine step must
    # stop the refined image from being sent and keep/restore the base.
    bot._pending_refine.clear()
    uid = 6013
    message = _message(uid, 85)
    status_msg = _status_message()
    event = asyncio.Event()
    event.job_id = "job13"

    base_msg = MagicMock()
    base_msg.delete = AsyncMock()
    base_msg.edit_reply_markup = AsyncMock()

    async def _refine_and_cancel(*_args):
        # Cancel arrives while the refine is still running.
        event.set()
        return (["/tmp/refined.png"], None)

    refine_mock = AsyncMock(side_effect=_refine_and_cancel)

    with patch.object(
        bot, "_send_comfyui_image", new_callable=AsyncMock, side_effect=[base_msg]
    ) as send_img:
        with patch.object(bot, "_generate_comfyui_refine", new=refine_mock):
            task = asyncio.create_task(
                bot._send_comfyui_output(
                    _comfyui_model(),
                    "/tmp/base.png",
                    "prompt",
                    status_msg,
                    message,
                    "Edit",
                    _regen_ctx(uid),
                    meta={"comfyui_remotes": ["/workspace/ComfyUI/output/base_a.png"]},
                    cancel_event=event,
                )
            )
            for _ in range(100):
                if bot._pending_refine:
                    break
                await asyncio.sleep(0)
            assert bot._pending_refine, "pending refine never registered"
            token = next(iter(bot._pending_refine))
            cb = _refine_callback(uid, f"refine:{token}:yes")
            await bot.handle_refine_decision(cb)
            result = await asyncio.wait_for(task, timeout=5)

    assert result is True
    refine_mock.assert_awaited_once()
    # the refined image is NOT sent — only the base went out
    assert send_img.await_count == 1
    # base is kept and restored to its regen keyboard, not deleted
    base_msg.delete.assert_not_awaited()
    kb = base_msg.edit_reply_markup.await_args.kwargs.get("reply_markup")
    assert kb == bot._image_regenerate_keyboard()


async def test_send_comfyui_output_confirm_yes_shows_refining_state_before_refine():
    # R2-5: on "yes" the base keyboard is swapped to _refining_keyboard() and
    # status_msg reads "Refinando…" BEFORE the refine step is awaited.
    bot._pending_refine.clear()
    uid = 6014
    message = _message(uid, 86)
    status_msg = _status_message()
    event = asyncio.Event()
    event.job_id = "job14"

    base_msg = MagicMock()
    base_msg.delete = AsyncMock()
    base_msg.edit_reply_markup = AsyncMock()
    refined_msg = MagicMock()
    refined_msg.delete = AsyncMock()

    async def _refine_after_state(*_args):
        # The "Refinando…" state must already be visible by the time the refine
        # is awaited (assertions inside the mock pin the ordering).
        base_msg.edit_reply_markup.assert_awaited_with(
            reply_markup=bot._refining_keyboard()
        )
        status_msg.edit_text.assert_awaited_with(
            "Refinando…", reply_markup=bot._cancel_job_keyboard(event)
        )
        return (["/tmp/refined.png"], None)

    with patch.object(
        bot, "_send_comfyui_image", new_callable=AsyncMock,
        side_effect=[base_msg, refined_msg],
    ) as send_img:
        with patch.object(bot, "_generate_comfyui_refine", new=_refine_after_state):
            task = asyncio.create_task(
                bot._send_comfyui_output(
                    _comfyui_model(),
                    "/tmp/base.png",
                    "prompt",
                    status_msg,
                    message,
                    "Edit",
                    _regen_ctx(uid),
                    meta={"comfyui_remotes": ["/workspace/ComfyUI/output/base_a.png"]},
                    cancel_event=event,
                )
            )
            for _ in range(100):
                if bot._pending_refine:
                    break
                await asyncio.sleep(0)
            assert bot._pending_refine, "pending refine never registered"
            token = next(iter(bot._pending_refine))
            cb = _refine_callback(uid, f"refine:{token}:yes")
            await bot.handle_refine_decision(cb)
            result = await asyncio.wait_for(task, timeout=5)

    assert result is True
    assert send_img.await_count == 2
    base_msg.delete.assert_awaited_once()


async def test_send_comfyui_output_confirm_yes_no_job_no_jobless_cancel_button():
    # No-job flows (reply/text-gen, cancel_event=None) must NOT render a jobless
    # "Cancelar" button on the "Refinando…" status: tapping it would cancel an
    # unrelated in-flight job (handle_cancel_job falls back to job_id=None → the
    # user's most recent job) and resolve every pending refine.
    bot._pending_refine.clear()
    uid = 6015
    message = _message(uid, 87)
    status_msg = _status_message()

    base_msg = MagicMock()
    base_msg.delete = AsyncMock()
    base_msg.edit_reply_markup = AsyncMock()
    refined_msg = MagicMock()
    refined_msg.delete = AsyncMock()

    async def _refine_after_state(*_args):
        # The "Refinando…" status edit must carry NO cancel keyboard when there
        # is no job backing the refine (assertions inside the mock pin ordering).
        status_msg.edit_text.assert_awaited_with(
            "Refinando…", reply_markup=None
        )
        return (["/tmp/refined.png"], None)

    with patch.object(
        bot, "_send_comfyui_image", new_callable=AsyncMock,
        side_effect=[base_msg, refined_msg],
    ) as send_img:
        with patch.object(bot, "_generate_comfyui_refine", new=_refine_after_state):
            task = asyncio.create_task(
                bot._send_comfyui_output(
                    _comfyui_model(),
                    "/tmp/base.png",
                    "prompt",
                    status_msg,
                    message,
                    "Edit",
                    _regen_ctx(uid),
                    meta={"comfyui_remotes": ["/workspace/ComfyUI/output/base_a.png"]},
                    cancel_event=None,
                )
            )
            for _ in range(100):
                if bot._pending_refine:
                    break
                await asyncio.sleep(0)
            assert bot._pending_refine, "pending refine never registered"
            token = next(iter(bot._pending_refine))
            cb = _refine_callback(uid, f"refine:{token}:yes")
            await bot.handle_refine_decision(cb)
            result = await asyncio.wait_for(task, timeout=5)

    assert result is True
    assert send_img.await_count == 2
    status_msg.edit_text.assert_awaited_with(
        "Refinando…", reply_markup=None
    )


# --- handle_refine_decision idempotency / ownership -------------------------


async def test_refine_decision_stale_and_retap_idempotent():
    bot._pending_refine.clear()
    cb = _refine_callback(1, "refine:deadbeef:yes")
    await bot.handle_refine_decision(cb)
    assert cb.answer.await_args.kwargs.get("show_alert") is True

    bot._pending_refine.clear()
    future = asyncio.get_running_loop().create_future()
    bot._pending_refine["tok2"] = {
        "future": future,
        "user_id": 1,
        "message_id": 1,
        "job_id": None,
    }
    cb2 = _refine_callback(1, "refine:tok2:yes")
    await bot.handle_refine_decision(cb2)
    assert future.done() and future.result() is True

    # Re-tap of an already-resolved token = idempotent no-op (no InvalidStateError).
    cb3 = _refine_callback(1, "refine:tok2:yes")
    await bot.handle_refine_decision(cb3)
    assert future.result() is True


async def test_refine_decision_wrong_user_ignored():
    bot._pending_refine.clear()
    future = asyncio.get_running_loop().create_future()
    bot._pending_refine["tok3"] = {
        "future": future,
        "user_id": 1,
        "message_id": 1,
        "job_id": None,
    }
    cb = _refine_callback(2, "refine:tok3:yes")
    await bot.handle_refine_decision(cb)
    assert "No es tu confirmación" in cb.answer.await_args.args[0]
    assert not future.done()


async def test_refine_decision_malformed_and_non_yes():
    bot._pending_refine.clear()
    cb = _refine_callback(1, "refine:oops")
    await bot.handle_refine_decision(cb)
    assert "Acción inválida." in cb.answer.await_args.args[0]

    bot._pending_refine.clear()
    future = asyncio.get_running_loop().create_future()
    bot._pending_refine["tok_maybe"] = {
        "future": future,
        "user_id": 1,
        "message_id": 1,
        "job_id": None,
    }
    cb2 = _refine_callback(1, "refine:tok_maybe:maybe")
    await bot.handle_refine_decision(cb2)
    assert future.done() and future.result() is False


# --- cancel force-resolve + _finish_job cleanup -----------------------------


async def test_handle_cancel_job_resolves_pending_refine():
    bot._pending_refine.clear()
    event = bot._start_job(1, "edit")
    future = asyncio.get_running_loop().create_future()
    bot._pending_refine["tok4"] = {
        "future": future,
        "user_id": 1,
        "message_id": 1,
        "job_id": event.job_id,
    }
    cb = MagicMock()
    cb.from_user.id = 1
    cb.data = f"cancel_job:{event.job_id}"
    cb.answer = AsyncMock()
    cb.message = MagicMock(text="", caption=None, edit_text=AsyncMock())

    await bot.handle_cancel_job(cb)

    assert future.result() is bot._REFINE_CANCELLED


async def test_finish_job_cleans_pending_refine():
    bot._pending_refine.clear()
    event = bot._start_job(1, "edit")
    future = asyncio.get_running_loop().create_future()
    bot._pending_refine["tok5"] = {
        "future": future,
        "user_id": 1,
        "message_id": 1,
        "job_id": event.job_id,
    }

    bot._finish_job(1, event)

    assert "tok5" not in bot._pending_refine
    assert future.result() is False


async def test_cancel_pending_refines_scoped_by_job():
    bot._pending_refine.clear()
    ev_a = bot._start_job(1, "edit")
    ev_b = bot._start_job(1, "edit")
    fut_a = asyncio.get_running_loop().create_future()
    fut_b = asyncio.get_running_loop().create_future()
    bot._pending_refine["tok_a"] = {
        "future": fut_a, "user_id": 1, "message_id": 1, "job_id": ev_a.job_id,
    }
    bot._pending_refine["tok_b"] = {
        "future": fut_b, "user_id": 1, "message_id": 1, "job_id": ev_b.job_id,
    }

    # Cancelling job A must leave job B's pending refine untouched.
    bot._cancel_pending_refines_for_user(1, job_id=ev_a.job_id)

    assert fut_a.done() and fut_a.result() is bot._REFINE_CANCELLED
    assert not fut_b.done()

    # job_id=None still resolves every pending refine for the user (fallback).
    bot._cancel_pending_refines_for_user(1)
    assert fut_b.done() and fut_b.result() is bot._REFINE_CANCELLED


async def test_handle_cancel_job_only_cancels_matching_job_refine():
    bot._pending_refine.clear()
    ev_a = bot._start_job(1, "edit")
    ev_b = bot._start_job(1, "edit")
    fut_a = asyncio.get_running_loop().create_future()
    fut_b = asyncio.get_running_loop().create_future()
    bot._pending_refine["tok_a"] = {
        "future": fut_a, "user_id": 1, "message_id": 1, "job_id": ev_a.job_id,
    }
    bot._pending_refine["tok_b"] = {
        "future": fut_b, "user_id": 1, "message_id": 1, "job_id": ev_b.job_id,
    }
    cb = MagicMock()
    cb.from_user.id = 1
    cb.data = f"cancel_job:{ev_a.job_id}"
    cb.answer = AsyncMock()
    cb.message = MagicMock(text="", caption=None, edit_text=AsyncMock())

    await bot.handle_cancel_job(cb)

    assert fut_a.done() and fut_a.result() is bot._REFINE_CANCELLED
    assert not fut_b.done()


# --- item 3: wiring meta/cancel_event across call sites + album routing -------


def _photo_message(*, caption=None, user_id=1001, chat_id=2001, message_id=1, file_id="p1"):
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


def _album_message(*, user_id=1001, chat_id=2001, message_id=1, file_id="p1"):
    msg = MagicMock()
    msg.from_user.id = user_id
    msg.chat.id = chat_id
    msg.message_id = message_id
    msg.caption = "cambia el fondo"
    msg.media_group_id = "mg-1"
    msg.photo = [MagicMock(file_id=file_id)]
    msg.answer = AsyncMock()
    msg.edit_text = AsyncMock()
    status = MagicMock()
    status.edit_text = AsyncMock()
    status.delete = AsyncMock()
    msg.reply = AsyncMock(return_value=status)
    return msg


_COMFYUI_REMOTES = {"comfyui_remotes": ["/workspace/ComfyUI/output/base_a.png"]}


async def test_regen_comfyui_passes_meta_and_cancel_event(generation_refs_file):
    uid = 9201
    sessions.set_comfyui_config(uid, model="krea2")
    bot.user_state[uid] = {"model": "comfyui"}
    regen = bot._build_image_regen_context(
        model=bot.get_model(uid),
        user_id=uid,
        prompt="blue moon",
        mode="text",
    )
    sessions.save_generation_ref(400, 88, provider="comfyui", prompt="blue moon", regen=regen)

    photo_msg = MagicMock()
    photo_msg.photo = [MagicMock()]
    photo_msg.chat.id = 400
    photo_msg.message_id = 88
    photo_msg.answer = AsyncMock(return_value=MagicMock())

    callback = MagicMock()
    callback.message = photo_msg
    callback.answer = AsyncMock()

    with patch.object(bot, "generate_image", new_callable=AsyncMock, return_value=(["/tmp/c.png"], None, dict(_COMFYUI_REMOTES))):
        with patch.object(bot, "_send_comfyui_output", new_callable=AsyncMock) as mock_send:
            await bot.handle_regenerate_image(callback)

    mock_send.assert_awaited_once()
    assert mock_send.await_args.kwargs["meta"] == _COMFYUI_REMOTES
    assert mock_send.await_args.kwargs["cancel_event"] is not None


async def test_text_gen_comfyui_passes_meta_cancel_none():
    msg = MagicMock()
    msg.from_user.id = 7034
    msg.answer = AsyncMock()
    model = _comfyui_model(key="comfyui", name="ComfyUI")

    with patch.object(bot, "generate_image", new_callable=AsyncMock, return_value=(["/tmp/c.png"], None, dict(_COMFYUI_REMOTES))):
        with patch.object(bot, "_send_comfyui_output", new_callable=AsyncMock) as mock_send:
            await bot._do_generate_text(msg, model, "cat")

    mock_send.assert_awaited_once()
    assert mock_send.await_args.kwargs["meta"] == _COMFYUI_REMOTES
    assert mock_send.await_args.kwargs["cancel_event"] is None


async def test_reply_edit_comfyui_passes_meta_cancel_none(sessions_file, generation_refs_file):
    uid = 8012
    sessions.set_comfyui_config(uid, model="krea2")
    bot.user_state[uid] = {"model": "comfyui"}

    reply_msg = MagicMock()
    reply_msg.photo = [MagicMock(file_id="fid")]
    reply_msg.chat.id = 300
    reply_msg.message_id = 50

    message = MagicMock()
    message.from_user.id = uid
    message.text = "cambia el fondo"
    message.reply_to_message = reply_msg
    message.answer = AsyncMock()

    with patch.object(bot, "_download_telegram_photo", new_callable=AsyncMock) as mock_dl:
        with patch.object(bot, "generate_image", new_callable=AsyncMock, return_value=(["/tmp/c.png"], None, dict(_COMFYUI_REMOTES))):
            with patch.object(bot, "_send_comfyui_output", new_callable=AsyncMock) as mock_send:
                await bot.handle_reply_edit(message)

    mock_dl.assert_awaited_once()
    mock_send.assert_awaited_once()
    assert mock_send.await_args.kwargs["meta"] == _COMFYUI_REMOTES
    assert mock_send.await_args.kwargs["cancel_event"] is None


async def test_variables_batch_comfyui_passes_meta_and_cancel_event(sessions_file, variables_file, monkeypatch):
    monkeypatch.setattr(bot, "COMFYUI_HOST", "1.2.3.4")
    bot.user_state[1001] = {"model": "comfyui"}
    msg = _photo_message(caption="/variables 2")
    msg.answer.return_value = _status_message()

    async def _fake_gen(model, prompt, image_data=None, **kwargs):
        return (["/tmp/v.png"], None, dict(_COMFYUI_REMOTES))

    with patch.object(bot, "generate_image", side_effect=_fake_gen):
        with patch.object(bot, "_send_comfyui_output", new_callable=AsyncMock, side_effect=[True, True]) as mock_send:
            with patch.object(bot, "process_image_result", new_callable=AsyncMock) as mock_proc:
                with patch(
                    "variables_store.random_combination",
                    return_value=("de pie, frontal, mirando", ("de pie", "frontal", "mirando")),
                ):
                    await bot._run_variables_batch(msg, 2, BytesIO(b"img"), None, source_file_id="p1")

    assert mock_send.await_count == 2
    for call in mock_send.await_args_list:
        assert call.kwargs["meta"] == _COMFYUI_REMOTES
        assert call.kwargs["cancel_event"] is not None
        assert call.kwargs["delete_status"] is False
    mock_proc.assert_not_awaited()
    last_text = msg.answer.return_value.edit_text.call_args.args[0]
    assert "Listo: 2/2" in last_text


async def test_album_batch_comfyui_routes_to_send_comfyui_output(sessions_file, generation_refs_file, monkeypatch):
    monkeypatch.setattr(bot, "COMFYUI_HOST", "1.2.3.4")
    uid = 1101
    sessions.set_comfyui_config(uid, model="krea2")
    bot.user_state[uid] = {"model": "comfyui"}
    anchor_msg = _album_message(user_id=uid, message_id=7, file_id="p1")

    async def _fake_gen(model, prompt, image_data=None, **kwargs):
        return (["/tmp/a.png"], None, dict(_COMFYUI_REMOTES))

    with patch.object(bot, "_download_telegram_file_id", new_callable=AsyncMock):
        with patch.object(bot, "generate_image", side_effect=_fake_gen):
            with patch.object(bot, "_send_comfyui_output", new_callable=AsyncMock, side_effect=[True, True]) as mock_send:
                with patch.object(bot, "process_image_result", new_callable=AsyncMock) as mock_proc:
                    await bot._process_album_edit_from_file_ids(anchor_msg, "cambia el fondo", ["p1", "p2"])

    assert mock_send.await_count == 2
    for call in mock_send.await_args_list:
        assert call.kwargs["meta"] == _COMFYUI_REMOTES
        assert call.kwargs["cancel_event"] is not None
        assert call.kwargs["delete_status"] is False
    mock_proc.assert_not_awaited()
    last_text = anchor_msg.reply.return_value.edit_text.call_args.args[0]
    assert "Completadas 2/2" in last_text


async def test_variables_batch_comfyui_chain_continues_after_decision(sessions_file, variables_file, monkeypatch):
    monkeypatch.setattr(bot, "COMFYUI_HOST", "1.2.3.4")
    bot.user_state[1001] = {"model": "comfyui"}
    msg = _photo_message(caption="/variables 2")
    msg.answer.return_value = _status_message()

    async def _fake_gen(model, prompt, image_data=None, **kwargs):
        return (["/tmp/v.png"], None, dict(_COMFYUI_REMOTES))

    base_msg = MagicMock()
    base_msg.delete = AsyncMock()
    base_msg.edit_reply_markup = AsyncMock()
    refined_msg = MagicMock()
    refined_msg.delete = AsyncMock()
    refined_msg.edit_reply_markup = AsyncMock()

    refine_mock = AsyncMock(return_value=(["/tmp/refined.png"], None))

    with patch.object(bot, "generate_image", side_effect=_fake_gen):
        with patch.object(
            bot, "_send_comfyui_image", new_callable=AsyncMock,
            side_effect=[base_msg, refined_msg, base_msg],
        ) as send_img:
            with patch.object(bot, "_generate_comfyui_refine", new=refine_mock):
                with patch(
                    "variables_store.random_combination",
                    return_value=("de pie, frontal, mirando", ("de pie", "frontal", "mirando")),
                ):
                    task = asyncio.create_task(
                        bot._run_variables_batch(msg, 2, BytesIO(b"img"), None, source_file_id="p1")
                    )
                    # item 1: confirm refine (yes)
                    for _ in range(200):
                        if bot._pending_refine:
                            break
                        await asyncio.sleep(0)
                    assert bot._pending_refine, "pending refine (item 1) never registered"
                    token1 = next(iter(bot._pending_refine))
                    await bot.handle_refine_decision(_refine_callback(1001, f"refine:{token1}:yes"))
                    # item 2: keep the base (no)
                    for _ in range(200):
                        if any(t != token1 for t in bot._pending_refine):
                            break
                        await asyncio.sleep(0)
                    token2 = next(t for t in bot._pending_refine if t != token1)
                    await bot.handle_refine_decision(_refine_callback(1001, f"refine:{token2}:no"))
                    await asyncio.wait_for(task, timeout=10)

    refine_mock.assert_awaited_once()
    assert send_img.await_count >= 3
    last_text = msg.answer.return_value.edit_text.call_args.args[0]
    assert "Listo: 2/2" in last_text


# --- item 4: deferred branches (a)-(f) with the REAL choke -------------------


async def test_send_comfyui_output_confirm_cancel_removes_keyboard():
    # (a) Cancel with the decision force-resolved: the base's keyboard goes to
    # None (NOT regen), no refine is started, the choke does not delete the
    # status, and the pending registry is clean.
    bot._pending_refine.clear()
    uid = 6021
    message = _message(uid, 91)
    status_msg = _status_message()
    event = bot._start_job(uid, "edit")

    base_msg = MagicMock()
    base_msg.delete = AsyncMock()
    base_msg.edit_reply_markup = AsyncMock()

    refine_mock = AsyncMock()

    with patch.object(
        bot, "_send_comfyui_image", new_callable=AsyncMock, side_effect=[base_msg]
    ) as send_img:
        with patch.object(bot, "_generate_comfyui_refine", new=refine_mock):
            task = asyncio.create_task(
                bot._send_comfyui_output(
                    _comfyui_model(),
                    "/tmp/base.png",
                    "prompt",
                    status_msg,
                    message,
                    "Edit",
                    _regen_ctx(uid),
                    meta=dict(_COMFYUI_REMOTES),
                    cancel_event=event,
                )
            )
            for _ in range(200):
                if bot._pending_refine:
                    break
                await asyncio.sleep(0)
            assert bot._pending_refine, "pending refine never registered"
            token = next(iter(bot._pending_refine))
            cb = MagicMock()
            cb.from_user.id = uid
            cb.data = f"cancel_job:{event.job_id}"
            cb.answer = AsyncMock()
            cb.message = status_msg
            await bot.handle_cancel_job(cb)
            result = await asyncio.wait_for(task, timeout=5)

    assert result is True
    refine_mock.assert_not_awaited()
    assert send_img.await_count == 1
    base_msg.edit_reply_markup.assert_awaited_once()
    assert base_msg.edit_reply_markup.await_args.kwargs.get("reply_markup") is None
    base_msg.delete.assert_not_awaited()
    status_msg.delete.assert_not_awaited()
    assert not bot._pending_refine


async def test_send_comfyui_output_confirm_refine_error_single_keeps_base():
    # (b-single) _generate_comfyui_refine -> (None, err): the base is kept and
    # restored to the regen keyboard, the status surfaces the error.
    bot._pending_refine.clear()
    uid = 6022
    message = _message(uid, 92)
    status_msg = _status_message()
    event = asyncio.Event()
    event.job_id = "jobb1"

    base_msg = MagicMock()
    base_msg.delete = AsyncMock()
    base_msg.edit_reply_markup = AsyncMock()

    refine_mock = AsyncMock(return_value=(None, "Configuración de refino inválida"))

    with patch.object(
        bot, "_send_comfyui_image", new_callable=AsyncMock, side_effect=[base_msg]
    ) as send_img:
        with patch.object(bot, "_generate_comfyui_refine", new=refine_mock):
            task = asyncio.create_task(
                bot._send_comfyui_output(
                    _comfyui_model(),
                    "/tmp/base.png",
                    "prompt",
                    status_msg,
                    message,
                    "Edit",
                    _regen_ctx(uid),
                    meta=dict(_COMFYUI_REMOTES),
                    cancel_event=event,
                )
            )
            for _ in range(200):
                if bot._pending_refine:
                    break
                await asyncio.sleep(0)
            assert bot._pending_refine, "pending refine never registered"
            token = next(iter(bot._pending_refine))
            await bot.handle_refine_decision(_refine_callback(uid, f"refine:{token}:yes"))
            result = await asyncio.wait_for(task, timeout=5)

    assert result is True
    refine_mock.assert_awaited_once()
    assert send_img.await_count == 1
    last_status = status_msg.edit_text.call_args
    assert "Configuración de refino inválida" in last_status.args[0]
    assert last_status.kwargs.get("reply_markup") is None
    kb = base_msg.edit_reply_markup.await_args.kwargs.get("reply_markup")
    assert kb == bot._image_regenerate_keyboard()
    base_msg.delete.assert_not_awaited()


async def test_send_comfyui_output_confirm_refine_error_album_keeps_base():
    # (b-album) _generate_comfyui_refine -> (None, err): the confirm prompt is
    # deleted, the base album is kept, and the status surfaces the error.
    bot._pending_refine.clear()
    uid = 6023
    message = _message(uid, 93)
    confirm_msg = MagicMock()
    confirm_msg.delete = AsyncMock()
    confirm_msg.edit_text = AsyncMock()
    message.answer = AsyncMock(return_value=confirm_msg)
    status_msg = _status_message()
    event = asyncio.Event()
    event.job_id = "jobb2"

    base_msgs = [MagicMock(), MagicMock()]
    for m in base_msgs:
        m.delete = AsyncMock()

    refine_mock = AsyncMock(return_value=(None, "El refino no produjo imágenes"))

    with patch.object(
        bot, "_send_comfyui_album", new_callable=AsyncMock, side_effect=[base_msgs]
    ) as send_album:
        with patch.object(bot, "_generate_comfyui_refine", new=refine_mock):
            task = asyncio.create_task(
                bot._send_comfyui_output(
                    _comfyui_model(),
                    ["/tmp/a.png", "/tmp/b.png"],
                    "prompt",
                    status_msg,
                    message,
                    "Edit",
                    _regen_ctx(uid),
                    meta=dict(_COMFYUI_REMOTES),
                    cancel_event=event,
                )
            )
            for _ in range(200):
                if bot._pending_refine:
                    break
                await asyncio.sleep(0)
            assert bot._pending_refine, "pending refine never registered"
            token = next(iter(bot._pending_refine))
            await bot.handle_refine_decision(_refine_callback(uid, f"refine:{token}:yes"))
            result = await asyncio.wait_for(task, timeout=5)

    assert result is True
    refine_mock.assert_awaited_once()
    assert send_album.await_count == 1
    confirm_msg.delete.assert_awaited_once()
    assert "El refino no produjo imágenes" in status_msg.edit_text.call_args.args[0]
    for m in base_msgs:
        m.delete.assert_not_awaited()


@pytest.mark.parametrize("trigger", ["no", "timeout"])
async def test_send_comfyui_output_confirm_album_final_image(monkeypatch, trigger):
    # (c) Album + falsy decision (no, or TTL=0) -> the confirm prompt is edited
    # to "Imagen final." (no keyboard), the status is deleted, no refine runs,
    # and the base album is kept.
    bot._pending_refine.clear()
    if trigger == "timeout":
        monkeypatch.setattr(bot, "REFINE_CONFIRM_TIMEOUT", 0)
    uid = 6024
    message = _message(uid, 94)
    confirm_msg = MagicMock()
    confirm_msg.delete = AsyncMock()
    confirm_msg.edit_text = AsyncMock()
    message.answer = AsyncMock(return_value=confirm_msg)
    status_msg = _status_message()
    event = asyncio.Event()
    event.job_id = "jobb3"

    base_msgs = [MagicMock(), MagicMock()]
    for m in base_msgs:
        m.delete = AsyncMock()

    refine_mock = AsyncMock()

    with patch.object(
        bot, "_send_comfyui_album", new_callable=AsyncMock, side_effect=[base_msgs]
    ) as send_album:
        with patch.object(bot, "_generate_comfyui_refine", new=refine_mock):
            task = asyncio.create_task(
                bot._send_comfyui_output(
                    _comfyui_model(),
                    ["/tmp/a.png", "/tmp/b.png"],
                    "prompt",
                    status_msg,
                    message,
                    "Edit",
                    _regen_ctx(uid),
                    meta=dict(_COMFYUI_REMOTES),
                    cancel_event=event,
                )
            )
            if trigger == "no":
                for _ in range(200):
                    if bot._pending_refine:
                        break
                    await asyncio.sleep(0)
                assert bot._pending_refine, "pending refine never registered"
                token = next(iter(bot._pending_refine))
                await bot.handle_refine_decision(_refine_callback(uid, f"refine:{token}:no"))
            result = await asyncio.wait_for(task, timeout=5)

    assert result is True
    refine_mock.assert_not_awaited()
    assert send_album.await_count == 1
    assert confirm_msg.edit_text.await_args.args[0] == "Imagen final."
    assert confirm_msg.edit_text.await_args.kwargs.get("reply_markup") in (None,)
    status_msg.delete.assert_awaited_once()
    for m in base_msgs:
        m.delete.assert_not_awaited()


async def test_send_comfyui_output_meta_none_skips_refine_choke():
    # (d) meta=None with comfyui_refine="1": the confirm choke is skipped and the
    # base is sent directly (pre-item behavior). The caller leaves the keyboard
    # to the real _send_comfyui_image default (regen keyboard) — no confirm kb.
    bot._pending_refine.clear()
    uid = 6025
    message = _message(uid, 95)
    status_msg = _status_message()

    with patch.object(
        bot, "_send_comfyui_image", new_callable=AsyncMock, return_value=MagicMock()
    ) as send_img:
        with patch.object(bot, "_send_comfyui_confirm_refine", new_callable=AsyncMock) as confirm_refine:
            ok = await bot._send_comfyui_output(
                _comfyui_model(),
                "/tmp/base.png",
                "prompt",
                status_msg,
                message,
                "Edit",
                _regen_ctx(uid),
            )

    assert ok is True
    assert send_img.await_count == 1
    confirm_refine.assert_not_awaited()
    assert not bot._pending_refine
    assert "reply_markup" not in send_img.await_args.kwargs


async def test_album_batch_comfyui_chain_real_choke(sessions_file, generation_refs_file, monkeypatch):
    # (e) Album batch (2 photos) with the REAL choke: each photo is a single
    # image with the confirm keyboard on it; the chain pauses per item. Item 1
    # confirms (yes) and refines; item 2 keeps the base (no). No hang.
    monkeypatch.setattr(bot, "COMFYUI_HOST", "1.2.3.4")
    uid = 1102
    sessions.set_comfyui_config(uid, model="krea2")
    bot.user_state[uid] = {"model": "comfyui"}
    anchor_msg = _album_message(user_id=uid, message_id=8, file_id="p1")

    async def _fake_gen(model, prompt, image_data=None, **kwargs):
        return (["/tmp/a.png"], None, dict(_COMFYUI_REMOTES))

    base1 = MagicMock()
    base1.delete = AsyncMock()
    base1.edit_reply_markup = AsyncMock()
    refined1 = MagicMock()
    refined1.delete = AsyncMock()
    refined1.edit_reply_markup = AsyncMock()
    base2 = MagicMock()
    base2.delete = AsyncMock()
    base2.edit_reply_markup = AsyncMock()

    refine_mock = AsyncMock(return_value=(["/tmp/refined.png"], None))

    with patch.object(bot, "_download_telegram_file_id", new_callable=AsyncMock):
        with patch.object(bot, "generate_image", side_effect=_fake_gen):
            with patch.object(
                bot, "_send_comfyui_image", new_callable=AsyncMock,
                side_effect=[base1, refined1, base2],
            ) as send_img:
                with patch.object(bot, "_generate_comfyui_refine", new=refine_mock):
                    with patch.object(bot, "process_image_result", new_callable=AsyncMock) as mock_proc:
                        task = asyncio.create_task(
                            bot._process_album_edit_from_file_ids(anchor_msg, "cambia el fondo", ["p1", "p2"])
                        )
                        # item 1: confirm refine (yes)
                        for _ in range(200):
                            if bot._pending_refine:
                                break
                            await asyncio.sleep(0)
                        assert bot._pending_refine, "pending refine (item 1) never registered"
                        token1 = next(iter(bot._pending_refine))
                        await bot.handle_refine_decision(_refine_callback(uid, f"refine:{token1}:yes"))
                        # item 2: keep the base (no)
                        for _ in range(200):
                            if any(t != token1 for t in bot._pending_refine):
                                break
                            await asyncio.sleep(0)
                        assert any(t != token1 for t in bot._pending_refine), \
                            "pending refine (item 2) never registered"
                        token2 = next(t for t in bot._pending_refine if t != token1)
                        await bot.handle_refine_decision(_refine_callback(uid, f"refine:{token2}:no"))
                        result = await asyncio.wait_for(task, timeout=10)

    assert result is True
    refine_mock.assert_awaited_once()
    assert send_img.await_count == 3
    mock_proc.assert_not_awaited()
    last_text = anchor_msg.reply.return_value.edit_text.call_args.args[0]
    assert "Completadas 2/2" in last_text
    assert not bot._pending_refine


async def test_variables_batch_comfyui_cancel_mid_chain_stops_clean(sessions_file, variables_file, monkeypatch):
    # (f) Cancel during the /variables chain: the loop stops cleanly with
    # "⏹ Cancelado. Completadas X/N" (no "Listo:"), item 2 never generates,
    # and the job is finished. The exact X is not pinned (A6).
    monkeypatch.setattr(bot, "COMFYUI_HOST", "1.2.3.4")
    bot.user_state[1001] = {"model": "comfyui"}
    msg = _photo_message(caption="/variables 2")
    msg.answer.return_value = _status_message()

    gen_calls = []

    async def _fake_gen(model, prompt, image_data=None, **kwargs):
        gen_calls.append(prompt)
        return (["/tmp/v.png"], None, dict(_COMFYUI_REMOTES))

    base_msg = MagicMock()
    base_msg.delete = AsyncMock()
    base_msg.edit_reply_markup = AsyncMock()

    refine_mock = AsyncMock()

    with patch.object(bot, "generate_image", side_effect=_fake_gen):
        with patch.object(
            bot, "_send_comfyui_image", new_callable=AsyncMock, side_effect=[base_msg]
        ) as send_img:
            with patch.object(bot, "_generate_comfyui_refine", new=refine_mock):
                with patch(
                    "variables_store.random_combination",
                    return_value=("de pie, frontal, mirando", ("de pie", "frontal", "mirando")),
                ):
                    task = asyncio.create_task(
                        bot._run_variables_batch(msg, 2, BytesIO(b"img"), None, source_file_id="p1")
                    )
                    # item 1 reaches the refine choke; cancel mid-chain.
                    for _ in range(200):
                        if bot._pending_refine:
                            break
                        await asyncio.sleep(0)
                    assert bot._pending_refine, "pending refine (item 1) never registered"
                    token1 = next(iter(bot._pending_refine))
                    job = next(j for j in bot._active_jobs[1001] if j["kind"] == "variables")
                    cb = MagicMock()
                    cb.from_user.id = 1001
                    cb.data = f"cancel_job:{job['id']}"
                    cb.answer = AsyncMock()
                    cb.message = msg.answer.return_value
                    await bot.handle_cancel_job(cb)
                    await asyncio.wait_for(task, timeout=10)

    refine_mock.assert_not_awaited()
    assert len(gen_calls) == 1  # item 2 must NOT generate
    assert send_img.await_count == 1
    last_text = msg.answer.return_value.edit_text.call_args.args[0]
    assert last_text.startswith("⏹ Cancelado.")
    assert "Completadas" in last_text
    assert "Listo:" not in last_text
    assert 1001 not in bot._active_jobs
    assert not bot._pending_refine


async def test_send_comfyui_image_caption_includes_prompt_when_flag_set(
    tmp_path, generation_refs_file
):
    image_path = tmp_path / "base.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    uid = 7001
    message = _message(uid, 91)
    message.chat.id = 9001
    message.answer_photo = AsyncMock(return_value=MagicMock(message_id=100))
    status_msg = _status_message()
    model = _comfyui_model()

    sent = await bot._send_comfyui_image(
        str(image_path), "de pie de frente", status_msg, message, "Variables 1/1",
        _regen_ctx(uid), model=model, delete_status=False,
        caption_prompt=True,
    )

    assert sent is not None
    caption = message.answer_photo.await_args.kwargs["caption"]
    assert "<b>Prompt:</b> de pie de frente" in caption


async def test_send_comfyui_image_caption_omits_prompt_by_default(
    tmp_path, generation_refs_file
):
    image_path = tmp_path / "base2.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    uid = 7002
    message = _message(uid, 92)
    message.chat.id = 9002
    message.answer_photo = AsyncMock(return_value=MagicMock(message_id=101))
    status_msg = _status_message()
    model = _comfyui_model()

    await bot._send_comfyui_image(
        str(image_path), "no debe salir", status_msg, message, "Edit",
        _regen_ctx(uid), model=model, delete_status=False,
    )

    caption = message.answer_photo.await_args.kwargs["caption"]
    assert "no debe salir" not in caption
