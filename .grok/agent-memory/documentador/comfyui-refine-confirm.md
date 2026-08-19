# Pool Documentation: comfyui-refine-confirm

**Items:** 4
**Date:** 2026-08-19
**Mode:** Feature GSD-lite (quick plans) — pool close, documentation only
**Plans:** `.planning/quick/20260819-comfyui-refine-confirm-item{1..4}/PLAN.md`
**Summaries:** `.planning/quick/20260819-comfyui-refine-confirm-item{1..4}/SUMMARY.md`

## Consolidated Outcome

The ComfyUI image generation/edit flow now supports an interactive refine pause: generate a
base → keyboard `[✨ Refinar][⏭ Continuar]` → refine the SAME base via `REFINE_ONLY` on the
box, or Continue = base final; TTL 300s (`REFINE_CONFIRM_TIMEOUT`); cancel respects the
`/variables` chain. Two-stage orchestration lives in `bot.py`; the refine execution lives on
the box (`gen_comfy.py`).

### Items

1. **Box `REFINE_ONLY` (`comfyui-vast-setup`)** — `REFINE_ONLY=1` + `REFINE_INPUT` (CSV of
   bases) refines pre-generated bases without regenerating; exit 0/2/3, stdout only paths.
   Harness `tests_refine_only.py` (16 tests, no box).
2. **Bot core (`grok`)** — `_generate_comfyui` base-only + `meta["comfyui_remotes"]`;
   `_generate_comfyui_refine` (REFINE_ONLY + regex + timeout `1200*N+300`); choke in
   `_send_comfyui_output`; registry `_pending_refine` + `handle_refine_decision`; TTL 300s;
   force-resolve on cancel (B1 filters job_id); re-check post-refino (B2); "Refinando…" state.
3. **Chains (`grok`)** — choke wired in regen/text-gen/variables/reply + defensive comfyui
   branch in album. Fix: jobless "Cancelar" keyboard only when `cancel_event is not None`.
4. **Deferred tests + README (`grok`)** — branches (a)-(f) with REAL choke; README documents
   flow + env + manual deploy note.

## Commits

| Hash | Message | Item |
|------|---------|------|
| `384002c` | `feat(gen_comfy): add REFINE_ONLY mode to refine pre-generated bases` | 1 |
| `1be1d9f` | `fix(gen_comfy): harden REFINE_ONLY mode (image guard, isfile, input validation)` | 1 |
| `2ff0cfd` | `feat(comfyui): two-stage refine with interactive confirmation` | 2 |
| `a1c9f0d` | `fix(comfyui): cancel scoping + post-refine cancel + refine error mapping` | 2 |
| `f6f7f63` | `fix(comfyui): surface refined-send failure + pin post-refine cancel test` | 2 |
| `378708d` | `feat(comfyui): wire refine confirmation into batch chains` | 3 |
| `71d8beb` | `fix(comfyui): avoid jobless cancel during refine in no-job flows` | 3 |
| `2afb4f1` | `chore(comfyui): drop stale line ref in album comment` | 3 |
| `05c70c0` | `feat(comfyui): cover refine deferral branches + README` | 4 |
| `19cd138` | `chore(comfyui): pin album-no keyless edit assert + scope cancel README claim` | 4 |

All conventional, no `Co-Authored-By`. Clean product tree in both repos (`grok` +
`comfyui-vast-setup`).

## Verifications

| Gate | Result | Source |
|------|--------|--------|
| Item 1 harness | 16 passed (`tests_refine_only.py`, no box) | executor + test-guardian |
| Item 1 bot regression | 138 passed (`test_variables_command` + `test_kie_provider`) | review handoff |
| Item 2 harness | 12 passed (audit) → 29 passed (final) | test-guardian + review handoff |
| Item 2 full suite | 428 passed, 2 skipped (final) | planner item 3 baseline |
| Item 3 harness | 35 → 36 passed; chain subset 95 passed | arch + test-guardian |
| Item 3 full suite | 435 passed, 2 skipped (final) | review handoff |
| Item 4 harness | 44 passed | arch + test-guardian + review |
| Item 4 full suite | **443 passed, 2 skipped** | arch + test-guardian |
| Arch | 4× PASS (item 4 PASS WITH NOTES, 2 benign) | arch-enforcer reports |
| Test-guardian | 4× SUITE PROTEGE ADECUADAMENTE; 0 mocks prohibidos | test-guardian reports |
| Review | all items 0 open issues (item1: 2 rounds; item2/3: 3; item4: 2) | review reports |

## Review stats

| Item | Effort | Rounds | Bugs | Suggestions | Nits | Exit |
|------|--------|--------|------|-------------|------|------|
| 1 | 3 | 2 | 0 | 6 (fixed) | 4 (fixed) | 0 open |
| 2 | 3 | 3 | 2 (B1, B2 — fixed) | 14 (fixed) | 9 (fixed) | 0 open |
| 3 | 3 | 3 | 1 (I3-B1 jobless cancel — fixed) | 1 (fixed) | 5 (fixed) | 0 open |
| 4 | 3 | 2 | 0 | 1 (fixed) | 9 (3 fixed, 6 wontfix) | 0 open |

## Learnings / Patterns

1. **Refine lives on the box; the bot orchestrates.** `gen_comfy.py` is the only refine
   executor. The bot does 2 steps: base-only generation → `REFINE_ONLY` + `REFINE_INPUT` for
   the refine. Never re-run generation to refine.
2. **`_seed()` is time-based → re-generating a base = a different image.** That is WHY the
   refine reuses the SAME base via `REFINE_INPUT` instead of regenerating. Do not "just
   regenerate + refine" — it changes the image.
3. **aiogram concurrency (`handle_as_tasks=True`) is safe for this pattern.** A handler can
   `await` an `asyncio.Future` that another callback resolves (chain handler + cancel/refine
   callbacks on the same loop). Pre-condition: the resolving task must not depend on the
   blocked one (callbacks only `callback.answer` + resolve).
4. **Jobless "Cancelar" gotcha.** In flows WITHOUT a job (reply edit, text-gen), a Cancel
   button would cancel an UNRELATED job. Render the cancel keyboard only when
   `cancel_event is not None`.
5. **`meta` is a safe side channel.** `generate_image`'s `meta` can carry
   `comfyui_remotes` without breaking consumers — every consumer uses `.get()` defensively;
   `retryable`/`exhausted` keys are only read when `err` is truthy.
6. **Editing without `reply_markup` removes the keyboard** (known repo gotcha). Every edit in
   the choke point re-applies `reply_markup` via `safe_edit_text`/`edit_reply_markup`. Single
   swaps the regen kb; album puts the keyboard in a SEPARATE text message (Telegram does not
   support inline keyboards on `sendMediaGroup`).
7. **Album comfyui multi-photo is NOT routed by `handle_album`.** The comfyui album branch is
   defensive/dead today; README explicitly does not claim multi-photo comfyui albums work.
8. **TTL prevents stuck job slots.** `asyncio.wait_for(future, REFINE_CONFIRM_TIMEOUT=300s)`
   → no decision = base final. Cancel force-resolves to `_REFINE_CANCELLED` (filter by job_id);
   `_finish_job` sweeps orphans.
9. **Regex path validation, fail-closed.** `^/workspace/[A-Za-z0-9_./-]{1,300}$` before
   building `REFINE_INPUT`; `REFINE_ONLY` exits 2 (no input) / 3 (no existing base) with
   stderr, and a refine exception keeps the base (never passes a missing base as "refined").
10. **Refine timeout scales with bases.** Box `_run_graph(timeout=1200)` is per base; the bot
    uses `1200*N+300`. `_comfyui_run_remote` must catch `subprocess.TimeoutExpired` (it now
    does) instead of surfacing a cryptic error.
11. **Choke activation.** `comfyui_refine=="1"` AND `meta is not None` AND non-empty
    `comfyui_remotes` AND not video. No `meta` → pre-item behavior (base sent directly).
12. **Test with the REAL choke.** Deferred tests mock only external borders
    (`_generate_comfyui_refine`, senders, `generate_image`, TTL via
    `REFINE_CONFIRM_TIMEOUT=0`); the decision/cancel logic runs real. The single (d) bypass
    test mocks the choke as a spy for `assert_not_awaited()` — legitimate because (a)-(f)
    cover the real choke.

## Residuals

Persisted: `.grok/agent-memory/residuals/comfyui-refine-confirm.md`

- **handoff (CRITICAL):** deploy `gen_comfy.py` to the Vast box
  (`cp /home/ubuntu/comfyui-vast-setup/gen_comfy.py /workspace/gen_comfy.py`) BEFORE the flow
  works in prod. Manual operator action; documented in bot + setup README.
- **in-scope-followup (deferred):** extend `handle_album` to route comfyui multi-photo albums
  (defensive/dead branch today). Do not reopen without new product scope.
- **out-of-scope:** pool pipeline artifacts (`.grok/agent-memory/*`,
  `.planning/quick/20260819-comfyui-refine-confirm-itemN/*`, `gsd-*.log`) — committed here as
  docs. `variables_extract/` (pre-existing) NOT touched.

## Roadmap Updates

None. No `HARDENING_ROADMAP.md` / `ROADMAP.md` / `decisions.md` in this repo. Pool close lives
in SUMMARYs + this file + the residuals file.

## Docs commit

`docs(comfyui): close refine-confirm pool` — documentation/pipeline artifacts only
(4× SUMMARY.md, PLAN.md files already present, residuals, this file, agent-memory reports,
gsd logs). Hash reported in the documentador close return. No code, no tests, no
`variables_extract/`, no binaries.

## Next Steps

- **Operator:** deploy `gen_comfy.py` to the Vast box before enabling the flow in production.
- Pool closed. No follow-up item required beyond the deferred `handle_album` routing (new
  product scope needed).
- Do not reopen the refine machinery (choke, TTL, B1/B2, wiring) without new scope.
- `variables_extract/` remains untracked/pre-existing; not part of this pool.
