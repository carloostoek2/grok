# SUMMARY: comfyui-refine-confirm — item 1 (Box REFINE_ONLY)

**Date:** 2026-08-19
**Pool:** comfyui-refine-confirm (item 1/4)
**Repo:** `/home/ubuntu/comfyui-vast-setup`
**Status:** Closed — harness green, no box required
**Commits:**
- `384002c` — `feat(gen_comfy): add REFINE_ONLY mode to refine pre-generated bases`
- `1be1d9f` — `fix(gen_comfy): harden REFINE_ONLY mode (image guard, isfile, input validation)`

## Outcome

`gen_comfy.py` gains a `REFINE_ONLY` mode: an early branch in `main()` that reads
`REFINE_INPUT` (CSV of base paths) and refines each pre-generated base with the same
`REFINE_FACES`/`DENOISE`/`STEPS`/`CFG` envs as the existing `REFINE=1` flow, printing only
refined paths to stdout and exiting 0/2/3. Base missing → skip + stderr (never passes a
missing base as "refined"); refine exception → base kept (existing main pattern). The
existing flow (txt2img / img2img / identity edit / video) stays byte-identical when
`REFINE_ONLY` is absent. Harness `tests_refine_only.py` runs without a box.

## Tasks completed

| Task | Result | Notes |
|------|--------|-------|
| 1. TDD RED | Done | Contract truths 1-6 as RED before impl |
| 2. `REFINE_ONLY` branch + exit 0/2/3 | Done | Early branch after `refine_cfg` (L188), before identity-edit comment; base-exists check before `run_refine`; try/except keeps base |
| 3. Harness `tests_refine_only.py` | Done | 16 tests green, no box; `tmp_path` real files, real `SystemExit`/`capsys` |
| 4. README (setup) | Done | Refine-only mode documented |
| 5. Commit work-unit | Done | `384002c` feat; `1be1d9f` fix/harden; no Co-Authored-By |

## Files changed (commits)

| File | What |
|------|------|
| `gen_comfy.py` | docstring header + `REFINE_ONLY`/`REFINE_INPUT` branch (refine pre-generated bases) |
| `README.md` | Refine-only mode section |
| `tests_refine_only.py` | New harness, 16 tests (no box) |

No-touch verified: `payloads/`, `workflows/`, `nodes/`, `scripts/`, `setup.sh`, `docs/`,
`inputs/`, `bot.py`.

## Deviations

None. Implemented exactly as specified in PLAN. The fix commit `1be1d9f` adds the
hardenings the PLAN called out (image guard, `os.path.isfile`, input validation).

## Verifications

```
Harness:  pytest tests_refine_only.py -q                 -> 16 passed (venv del proyecto)
Bot regression:  pytest tests/test_variables_command.py tests/test_kie_provider.py -q
                                                         -> 138 passed
```

## Residuals

| Title | Class | Why | Files |
|-------|-------|-----|-------|
| SSH timeout 600s (bot) vs `_run_graph(timeout=1200)` per base | out-of-scope | Mitigated in item 2 (bot passes a larger timeout). MEDIUM, pre-existing in `REFINE=1`. | `bot.py` |
| Shell quoting of `REFINE_INPUT` | out-of-scope | Hardened with regex in item 2 (bot, fail-closed). LOW. | `bot.py` |
| Deploy `gen_comfy.py` to the box | handoff (CRITICAL) | Manual operator action: `cp gen_comfy.py /workspace/gen_comfy.py` before the flow works in prod. | `comfyui-vast-setup` |

## Self-Check: PASSED

- [x] Todas las tareas completadas
- [x] Harness del PLAN corrido (16 passed)
- [x] Regresión del bot sin fallos (138 passed)
- [x] Flujo existente intacto (`test_without_refine_only_keeps_refine1_flow`)
- [x] Convenciones del proyecto respetadas

## Gates

| Step | Agent | Verdict | Source |
|------|-------|---------|--------|
| Impact | impact-analyzer | Done — viable, risk LOW with 3 mitigations | `.grok/agent-memory/impact-analyzer/comfyui-refine-confirm-item1.md` |
| Plan | gsd-planner | PLAN.md locked (A7-A10) | `.grok/agent-memory/gsd-planner/comfyui-refine-confirm-item1.md` |
| Execute | gsd-executor | Self-check PASSED (TDD RED → GREEN 16) | SUMMARY + `.planning/quick/gsd-comfyui-refine-confirm-item1.log` |
| Arch | arch-enforcer | **PASS**, 0 violations, harness 6/6 (audit time) | `.grok/agent-memory/arch-enforcer/comfyui-refine-confirm-item1.md` |
| Tests | test-guardian | **suite protege adecuadamente** (6/6 Truths, 0 mocks prohibidos) | `.grok/agent-memory/test-guardian/comfyui-refine-confirm-item1.md` |
| Review | reviewer | Effort **3**, **2 rounds**, **0 open issues** | `.grok/agent-memory/review/comfyui-refine-confirm-item1.md` |

## Review

| Field | Value |
|-------|-------|
| **Effort** | 3 (1 general + tests + plan) |
| **Rounds** | 2 |
| **Exit** | 0 open issues (round 2) |
| **Bugs** | 0 |
| **Suggestions** | 6 (round 1, fixed) |
| **Nits** | 4 (round 1, fixed) |

Round 1: 6 suggestions + 4 nits. Fix round: 9 fixed + 1 wontfix (harness RED depends on
`/workspace/payloads` — dev-runner-only by design). Round 2: 0 issues, all reviewers
APPROVE / CLEAN.
