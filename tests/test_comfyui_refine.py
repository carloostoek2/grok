"""ComfyUI refine-confirm flow (item 2): two-stage generation with interactive
confirmation. Covers base-only _generate_comfyui (meta with comfyui_remotes),
_generate_comfyui_refine (REFINE_ONLY=1, validated paths, scaled timeout),
the _send_comfyui_output choke -> _send_comfyui_confirm_refine decision flow,
handle_refine_decision idempotency, cancel force-resolve and _finish_job cleanup.

pytest.ini sets asyncio_mode=auto, so async tests need no marker.
"""

from __future__ import annotations

import asyncio
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
            return_value=["/workspace/ComfyUI/output/base_a.png"],
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
            return_value=["/workspace/ComfyUI/output/refined_base_a.png"],
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
    # base send
    assert send_img.await_args_list[0].args[0] == "/tmp/base.png"
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
    refined_call = send_album.await_args_list[1]
    assert refined_call.args[0] == ["/tmp/r1.png", "/tmp/r2.png"]
    assert refined_call.kwargs.get("delete_status") is True
    confirm_msg.delete.assert_awaited_once()
    for m in base_msgs:
        m.delete.assert_not_awaited()


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
