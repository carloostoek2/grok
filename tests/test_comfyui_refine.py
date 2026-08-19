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
from unittest.mock import AsyncMock, MagicMock, patch

import bot


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
