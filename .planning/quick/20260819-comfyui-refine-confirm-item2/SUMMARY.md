# SUMMARY: comfyui-refine-confirm — item 2 (Bot core: 2-stage gen + interactive refine)

**Date:** 2026-08-19
**Pool:** comfyui-refine-confirm (item 2/4)
**Repo:** `/home/ubuntu/repos/grok`
**Status:** Closed — harness + full suite green
**Commits:**
- `2ff0cfd` — `feat(comfyui): two-stage refine with interactive confirmation`
- `a1c9f0d` — `fix(comfyui): cancel scoping + post-refine cancel + refine error mapping`
- `f6f7f63` — `fix(comfyui): surface refined-send failure + pin post-refine cancel test`

## Outcome

Bot machinery for the interactive refine pause. `_generate_comfyui` becomes base-only
(no `REFINE=`) and returns `meta["comfyui_remotes"]` (remote paths) through `_generate_once`
→ `generate_image`. New `_generate_comfyui_refine` runs `REFINE_ONLY=1 REFINE_INPUT=<CSV>`
(validated with fail-closed regex `^/workspace/[A-Za-z0-9_./-]{1,300}$`), timeout
`1200*N+300`, and `_comfyui_run_remote` captures `TimeoutExpired`. The choke in
`_send_comfyui_output` (activates only with `comfyui_refine=="1"` + `meta` + remotes + not
video) shows `[✨ Refinar][⏭ Continuar]` (`refine:<token>:<yes|no>`), registry
`_pending_refine[token] = {future, user_id, message_id, job_id}`, `handle_refine_decision`
validates token+user and is idempotent. TTL `asyncio.wait_for(future,
REFINE_CONFIRM_TIMEOUT)` (default 300s) → no decision = base final. `handle_cancel_job`
force-resolves to `_REFINE_CANCELLED`; flow re-checks `_job_cancelled` post-await; `_finish_job`
sweeps orphans. Wired only `_process_single_photo_edit` (`meta=kie_meta, cancel_event=cancel_event`).

## Tasks completed

| Task | Result | Notes |
|------|--------|-------|
| 1. Base-only `_generate_comfyui` + `meta["comfyui_remotes"]` | Done | 3-tuple; `_generate_once` comfyui branch propagates meta; `generate_image` wire L1711 |
| 2. `_generate_comfyui_refine` (REFINE_ONLY + regex + timeout) | Done | Fail-closed path validation; `1200*N+300`; `_comfyui_run_remote` catches TimeoutExpired |
| 3. Registry `_pending_refine` + keyboard + `handle_refine_decision` | Done | Idempotent re-tap/stale; ownership check |
| 4. Choke `_send_comfyui_output` + `_send_comfyui_confirm_refine` | Done | yes → refine (single deletes base, album keeps base + new refined album); no/timeout → base final (single swap to regen kb; album confirm msg → "Imagen final.") |
| 5. Cancel force-resolve + `_finish_job` orphan sweep | Done | B1 filters by job_id; B2 re-checks post-refino; conftest clears registry |
| 6. Tests + regression + commit | Done | 12 tests (RED→GREEN) → 29 after fix rounds; full suite 428+2 |

## Files changed (commits)

| File | What |
|------|------|
| `bot.py` | Constants/registry, `_generate_comfyui` base-only+meta, `_generate_comfyui_refine`, choke + `_send_comfyui_confirm_refine`, keyboard + `handle_refine_decision`, cancel force-resolve, `_finish_job` sweep, `_send_comfyui_image`/`_send_comfyui_album` reply_markup/save_ref, `_process_single_photo_edit` wire |
| `tests/conftest.py` | `_pending_refine.clear()` in reset (1 line) |
| `tests/test_comfyui_refine.py` | New harness (12 → 29 tests) |

No-touch: 4 unwired call sites (1271/1476/2086/2346), `_process_album_edit_from_file_ids`,
variables, `_model_from_regen`, `get_model`, `_comfyui_is_video`, `process_image_result`,
`comfyui-vast-setup`.

## Deviations

One improvement over PLAN: in yes-single the impl unwraps `refined[0]` before
`_send_comfyui_image` (`single_refined = refined[0] if isinstance(refined, list) else refined`);
PLAN block 13 passed the raw list → `open(str(list))` would have failed. Impl fixed a latent
plan bug.

## Verifications

```
Harness (audit, 2ff0cfd):  pytest tests/test_comfyui_refine.py -q -> 12 passed
Harness (final):           -> 29 passed
Full suite (arch snapshot): -> 411 passed, 2 skipped
Full suite (final):        -> 428 passed, 2 skipped
Regression subset:  test_variables_command + test_cancel_job + test_kie_provider + test_round5
                         -> 165 passed
```

## Residuals

| Title | Class | Why | Files |
|-------|-------|-----|-------|
| Deferral branches (a) cancel-branch, (b) refine-failure, (c) album no/timeout, (d) choke-negative (meta=None) | in-scope-followup | Deferred to item 4 (tests) by PLAN | `tests/test_comfyui_refine.py` |
| `.grok/` / `.planning/` untracked artifacts | out-of-scope | Documentador consolidates at pool close | repo artifacts |
| Deploy `gen_comfy.py` to box (item 1) | handoff | Pending manual operator action | `comfyui-vast-setup` |

## Self-Check: PASSED

- [x] Todas las tareas completadas
- [x] Harness del PLAN corrido (12 RED→GREEN, 29 final)
- [x] Regresión completa sin fallos nuevos (428 passed, 2 skipped)
- [x] 4 call sites no-wired intactos (estado intermedio, wire en ítem 3)
- [x] Convenciones del proyecto respetadas

## Gates

| Step | Agent | Verdict | Source |
|------|-------|---------|--------|
| Impact | impact-analyzer | Done — viable, concurrency safe (`handle_as_tasks=True`), 3 requisitos no negociables | `.grok/agent-memory/impact-analyzer/comfyui-refine-confirm-item2.md` |
| Plan | gsd-planner | PLAN.md locked (A1-A11) | `.grok/agent-memory/gsd-planner/comfyui-refine-confirm-item2.md` |
| Execute | gsd-executor | Self-check PASSED (TDD RED → GREEN) | SUMMARY + `.planning/quick/gsd-comfyui-refine-confirm-item2.log` |
| Arch | arch-enforcer | **PASS**, 0 violations, harness 12/12 (audit time) | `.grok/agent-memory/arch-enforcer/comfyui-refine-confirm-item2.md` |
| Tests | test-guardian | **suite protege adecuadamente**; GAPS 1-5 deferred to item 4 | `.grok/agent-memory/test-guardian/comfyui-refine-confirm-item2.md` |
| Review | reviewer | Effort **3**, **3 rounds**, **0 open issues** | `.grok/agent-memory/review/comfyui-refine-confirm-item2.md` |

## Review

| Field | Value |
|-------|-------|
| **Effort** | 3 (1 general + tests + plan) |
| **Rounds** | 3 |
| **Exit** | 0 open issues (round 3) |
| **Bugs** | 2 (round 1, fixed) |
| **Suggestions** | 14 (round 1: 11; round 2: 3 — all fixed) |
| **Nits** | 9 (round 1: 7; round 2: 2 — fixed) |

- Round 1: 2 bugs (B1 cancel force-resolved ALL user decisions → fix: filter by job_id; B2
  cancel during refine ignored → fix: re-check post-refino), 11 suggestions, 7 nits. Fix
  round: 18 fixed + 2 wontfix.
- Round 2: 0 bugs, 3 suggestions, 2 nits (B2 without test, refined-send failure, etc.) →
  5 fixed.
- Round 3: 0 issues, all APPROVE / CLEAN.
