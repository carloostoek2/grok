# SUMMARY: variables-provider-global

**Date:** 2026-08-17
**Pool:** variables-provider-global (1 item)
**Status:** Closed — tests passing
**Commits:**
- `a22ddaea3b3c8e04a88ad347074a0262411ed9f0` — `feat(variables): run batch on configured image provider`
- `c7c66405ba57740de14106896b6c61957b93f2ea` — `test(variables): lock i2i image_data and kie reply batch path`

## Outcome

`/variables` batch image edits now use `get_model(uid)` — the same source of truth as regular photo edits — instead of rewriting every non-ComfyUI user onto Kie Grok Imagine. Configured xAI, Replicate Grok, Seedream, and ComfyUI image backends run as selected. Video (`grok_video`, ComfyUI `wan_i2v`) and Face Swap are rejected with a `/config` image-model message. Default remains `model=grok` + `grok_imagine_provider=kie`.

## Tasks completed

| Task | Result | Notes |
|------|--------|-------|
| 1. Failing tests (TDD RED) | Done | 10 failed on Kie-forcing / missing seek / always-prefer Kie ref |
| 2. `get_model` routing + rejects | Done | Helper `_variables_model_or_reject`; allowlist from provider; reply gate |
| 3. README + regression + full suite | Done | `/variables` no longer claims Kie-only |

## Files changed (commit)

| File | What |
|------|------|
| `bot.py` | Constants, `_variables_model_or_reject`, `_run_variables_batch` routing, `cmd_variables_reply` Kie-ref gate, comments/docstring |
| `tests/test_variables_command.py` | `_set_user_image_config`; retarget Kie-force tests; add xAI/Replicate/seedream/video/faceswap/reply/regen/credential coverage |
| `README.md` | `/variables` uses configured image model/provider |

## Deviations

None. Implemented exactly as specified in PLAN Exact implementation. No no-touch files edited.

## Verifications

```
Task 1 RED:  tests/test_variables_command.py -k "batch or cmd_variables_reply or kie_ref"
              10 failed, 17 passed (expected)

Task 2:      ./venv/bin/python -m pytest tests/test_variables_command.py -q
              41 passed

Task 3:      regression (variables + album + cancel + kie)
              196 passed
             ./venv/bin/python -m pytest tests/ -q
              375 passed, 2 skipped (live smoke, no LIVE_SMOKE=1)
```

## Residuals

| Title | Class | Why | Files |
|-------|-------|-----|-------|
| `_run_variables_batch` still exceeds 50 LOC | out-of-scope | telegram-bot-hardener flag already noted; PLAN A11 forbids a service extract in this slice. Helper `_variables_model_or_reject` kept the function from growing. Documented only — do not reopen this item. | `bot.py` |

Sources: executor SUMMARY residual; arch-enforcer observation (142 LOC, L1913–2054); review residual; test-guardian “out of DoD”.

## Self-Check: PASSED

- [x] Todas las tareas completadas
- [x] Tests del PLAN corridos
- [x] 0 regresiones atribuibles
- [x] Convenciones del proyecto respetadas

## Gates

| Step | Agent | Verdict | Source |
|------|-------|---------|--------|
| Impact | impact-analyzer | Done — routing alignment, medium risk | `.grok/agent-memory/impact-analyzer/variables-provider-global.md` |
| Plan | gsd-planner | PLAN.md (A1–A14 locked) | `.planning/quick/variables-provider-global/PLAN.md` |
| Execute | gsd-executor | Self-check PASSED (TDD RED 10 fail → GREEN 41) | `.planning/quick/gsd-variables-provider-global.log` |
| Arch | arch-enforcer | **PASS WITH NOTES**, 0 critical | `.grok/agent-memory/arch-enforcer/variables-provider-global.md` |
| Tests | test-guardian | **suite protege adecuadamente** (0 mocks prohibidos) | `.grok/agent-memory/test-guardian/variables-provider-global.md` |
| Review | reviewer | Effort **3**, **2 rounds**, **0 open issues** | `.grok/agent-memory/review/variables-provider-global.md` |
| Run | executor + review fix | **375 passed**, 2 skipped; after fix round **43 passed** in `test_variables_command.py` | SUMMARY + orchestrator close note |

## Review

| Field | Value |
|-------|-------|
| **Effort** | 3 (1 general + tests + plan alignment) |
| **Rounds** | 2 |
| **Exit** | 0 open issues (round 2) |
| **Bugs** | 0 |
| **Suggestions** | 3 (all Tests reviewer; all fixed) |
| **Nits** | 0 |

### Round 1 — [Tests] 3 suggestions (fixed in `c7c6640`)

1. Non-Kie batches must pass the original `image_data` BytesIO into `generate_image`.
2. Kie reply path with `image_data=None` (uses `kie_source_ref`) must stay locked.
3. ComfyUI `wan_i2v` reject must go through the real detector (not only a mocked `_comfyui_is_video` path).

Also included test-guardian hardening on existing regen tests (A13 both ways: forward `kie_source_ref` on kie; strip it on non-kie). Primary suite after fix: **43 passed**.

### Round 2

- [General]: 0
- [Tests]: 0
- [Plan]: 0
