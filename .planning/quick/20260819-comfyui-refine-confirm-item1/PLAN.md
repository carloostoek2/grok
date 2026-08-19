---
phase: quick
plan: comfyui-refine-confirm-item1
type: auto
item: Modo REFINE_ONLY en gen_comfy.py (box ComfyUI)
source: pool comfyui-refine-confirm
mode: standard
impact_ref: .grok/agent-memory/impact-analyzer/comfyui-refine-confirm-item1.md
test_command: cd /home/ubuntu/comfyui-vast-setup && /home/ubuntu/repos/grok/venv/bin/python -m pytest tests_refine_only.py -q
---

## Objective

`gen_comfy.py` (box ComfyUI Vast, vía SSH) gana un modo **refine-only**: dado `REFINE_ONLY` + `REFINE_INPUT` (paths CSV de bases ya generadas), refina cada base existente con la misma cascada `run_refine` (mismas env `REFINE_FACES`/`REFINE_DENOISE`/`REFINE_STEPS`/`REFINE_CFG`), imprime los paths refinados (una línea por archivo) y sale `0` con salidas, `3` sin salidas, `2` si `REFINE_ONLY` sin `REFINE_INPUT`. Bases inexistentes se omiten con stderr (NUNCA pasan como "refinadas"); un refino individual que lanza excepción conserva la base en la salida. Es el habilitador del ítem 2 del pool (bot: generar → confirmar → refinar la MISMA base en modo refine-only).

Este modo es **ortogonal**: el flujo actual (REFINE=1 completo, txt2img, img2img, identity edit, video wan/minimax) queda byte-idéntico si `REFINE_ONLY` está ausente. Cero degradación (REGLA #1 AGENTS.md).

## Scope

- **In:**
  - `gen_comfy.py` (`/home/ubuntu/comfyui-vast-setup/`, copia canónica byte-idéntica al box): branch temprano `REFINE_ONLY` en `main()` + docstring de cabecera (L2-13).
  - `README.md` del setup: documentar el modo refine-only (env, exit codes, ejemplo).
  - `tests_refine_only.py` nuevo en el setup (harness, corre SIN box).
- **Out / Non-goals:**
  - `bot.py` (`_generate_comfyui` L3274, `_comfyui_run_remote` L3199-3216, `_comfyui_ssh_base` L3182) — el bot se toca SOLO en ítem 2. No tocar aquí.
  - Timeout SSH 600s (bot.py:3210) vs `_run_graph(timeout=1200)`: NO se resuelve aquí — solo se documenta (impact MEDIUM). El ítem 2 mitigará con timeout mayor en la llamada refine-only.
  - Shell quoting de `REFINE_INPUT` (impact LOW): NO se hardeniza aquí; el bot lo hace con regex en ítem 2.
  - `payloads/`, `workflows/`, `nodes/`, `scripts/`, `setup.sh`, `docs/`, inputs/ — no-touch.
  - Limpieza de `/workspace/ComfyUI/output` entre runs SSH: fuera (impact: OK, los bases persisten; refino secuencial sin race).
- **Constraints:**
  - Un solo commit en el repo `comfyui-vast-setup` al final (conventional commit, sin `Co-Authored-By`).
  - El harness corre SIN box real: solo stdlib + monkeypatch de `run_refine`/`_run_graph`/stdin/env; `pytest.raises(SystemExit)`; assert de exit code y stdout.
  - El setup no tiene suite ni pytest.ini → pytest usa defaults; los tests son síncronos (sin asyncio). Runner: `/home/ubuntu/repos/grok/venv/bin/python -m pytest` (pytest 8.4.2 verificado).

## Assumptions

Cerradas por impact-analyzer — no reabrir:

- **A1:** El branch es ortogonal: se inserta en `main()` ANTES de la generación, tras leer `prompt_text` y las env `REFINE_FACES`/`DENOISE`/`STEPS`/`CFG` (gen_comfy.py L178-188). Solo se activa si `REFINE_ONLY` ∈ ("1","true","yes").
- **A2:** `REFINE_ONLY` no depende de `REFINE`/`do_refine`: si está activo, refina siempre; el gate de REFINE no se consulta.
- **A3:** `run_refine` (L160-170) devuelve `[base_path]` cuando la base NO existe — en REFINE_ONLY eso DEBE evitarse comprobando `os.path.exists(base)` ANTES de llamar, emitiendo stderr y saltando (no añadir a salida).
- **A4:** Exit codes: `0` salidas impresas, `2` error de entrada (`REFINE_ONLY` sin `REFINE_INPUT`), `3` sin salidas. Contrato del bot: stdout solo líneas `/workspace/...`.
- **A5:** Excepción en un refino individual → stderr + conservar el path base en la salida (mismo patrón que main() L459-461).
- **A6:** `REFINE_ONLY` hoy no lo pasa nadie (confirmado en impact: bot.py:3304-3313 solo pasa `REFINE`). Cambio seguro en el box.

Decisiones del planner (reversibles, locked para este slice):

- **A7:** Insertar el branch tras L188 (`refine_cfg = float(...)`), antes del comentario `# --- Krea 2 Identity Edit` (L190).
- **A8:** Naming de env y branch fiel al estilo del archivo: `refine_only` (mismo estilo que `do_refine` L183-184), split de `REFINE_INPUT` por coma con `.strip()` descartando vacíos.
- **A9:** El harness usa la ruta del identity-edit (`MODEL=krea2 LORA=krea_edit` + `INPUT_IMAGE` real en temp) para probar "flujo original intacto", porque construye el grafo en Python puro (L225-263) SIN depender de `/workspace/payloads/*.json`; solo se mockea `_run_graph`. Evita mockear `json.load`.
- **A10:** Los paths `REFINE_INPUT` del harness apuntan a archivos temporales reales (tmp_path); las bases "inexistentes" se representan con paths de temp que no existen. No se mockea `os.path.exists`.

## Architecture Approach

### QUÉ (behavior / contracts)

**Outcome:** con `REFINE_ONLY=1 REFINE_INPUT=a.png,b.png`, el script refina `a` y `b` (si existen) y su stdout son SOLO los paths refinados, uno por línea.

**Happy path**

1. Bot (ítem 2) corre `python3 /workspace/gen_comfy.py` con `REFINE_ONLY='1'` y `REFINE_INPUT='<bases CSV>'`, prompt por stdin.
2. `main()` lee `prompt_text` y las env de refino; detecta `REFINE_ONLY` → entra al branch temprano (no genera).
3. Por cada base de `REFINE_INPUT`: si existe → `run_refine(base, prompt_text, faces=refine_faces, denoise=refine_denoise, steps=refine_steps, cfg=refine_cfg)`; recolecta los paths refinados.
4. Imprime los paths recolectados (una línea por archivo); `sys.exit(0)`.

**Tabla de exit codes**

| Condición | stdout | stderr | exit |
|-----------|--------|--------|------|
| `REFINE_ONLY` sin `REFINE_INPUT` | vacío | mensaje explicativo con `REFINE_INPUT` | 2 |
| Todas las bases existen y refinan | paths refinados | — | 0 |
| Base inexistente | no incluida | `base no existe, se omite: <path>` | (sigue; 0 si queda output) |
| Todas las bases inexistentes | vacío | avisos por base | 3 |
| Refino individual lanza excepción | path base conservado | `refino falló para <base>: <e>` | 0 (si queda output) |
| `REFINE_ONLY` ausente | (flujo original) | — | (sin cambios) |

**Truths (must be true at the end)**

1. `REFINE_ONLY=1` + bases existentes → refina cada una, imprime los refinados, exit 0.
2. `REFINE_ONLY=1` + base inexistente → NO aparece en stdout, `run_refine` NO se llama para ella, stderr avisa.
3. `REFINE_ONLY=1` sin `REFINE_INPUT` → exit 2, stderr menciona `REFINE_INPUT`.
4. `REFINE_ONLY=1` + refino con excepción → la base se conserva en stdout, stderr con `refino falló`.
5. `REFINE_ONLY` ausente → REFINE=1 y todos los flujos actuales siguen intactos (byte-idénticos).
6. stdout del modo refine-only: solo líneas de paths (contrato del bot); sin mensajes.

### CÓMO (structure / patterns)

- **Layer:** dentro de `main()` en `gen_comfy.py` — script single-file, sin módulos nuevos.
- **Pattern to copy:**
  - `main()` L183-184 — parse de flag booleano de env (`in ("1","true","yes")`).
  - `main()` L194-196 / L301-303 / L306-308 — `sys.stderr.write(...)` + `sys.exit(2)` para error de entrada.
  - `main()` L454-463 — loop de refino con `try/except` que conserva el path base (`final_paths.append(p)`) y `out += run_refine(...)`.
  - `main()` L465-467 — print uno por línea + `sys.exit(0 if final_paths else 3)`.
  - `run_refine` L160-170 — firma de kwargs que el branch debe replicar.
- **Interfaces / types:** ningún tipo público nuevo. El branch usa solo stdlib (`os`, `sys`).

### Exact implementation

#### 1. Docstring de cabecera (`gen_comfy.py` L2-13)

Insertar tras la línea `REFINE_FACES=0 ...` (L10), antes de `Imprime el/los path(s)...` (L12):

```
  REFINE_ONLY=1         → refina bases YA generadas (sin regenerar); requiere REFINE_INPUT
  REFINE_INPUT=a.png,b.png → paths base CSV a refinar en modo REFINE_ONLY
                          Exit: 0 con salidas / 2 error de entrada / 3 sin salidas
```

#### 2. Branch temprano en `main()` — insertar tras L188 (`refine_cfg = float(...)`), antes del comentario L190

```python
    # --- Modo refine-only: refinar bases ya generadas (flujo 2-paso del bot) ---
    refine_only = os.environ.get("REFINE_ONLY", "").strip().lower() in ("1", "true", "yes")
    if refine_only:
        refine_input = os.environ.get("REFINE_INPUT", "").strip()
        if not refine_input:
            sys.stderr.write("REFINE_ONLY requiere REFINE_INPUT (paths CSV a refinar)\n")
            sys.exit(2)
        out = []
        for base in [x.strip() for x in refine_input.split(",") if x.strip()]:
            if not os.path.exists(base):
                sys.stderr.write(f"base no existe, se omite: {base}\n")
                continue
            try:
                out += run_refine(base, prompt_text, faces=refine_faces,
                                  denoise=refine_denoise, steps=refine_steps, cfg=refine_cfg)
            except Exception as e:
                sys.stderr.write(f"refino falló para {base}: {e}\n")
                out.append(base)
        for f in out:
            print(f)
        sys.exit(0 if out else 3)
```

Notas del patrón (no desviarse):
- El `try/except` replica main() L459-461: excepción → stderr + conservar base.
- El chequeo `os.path.exists(base)` es ANTES de `run_refine` → evita que `run_refine` devuelva `[base_path]` (L163-164) y que una base borrada pase como "refinada".
- `out += run_refine(...)` soporta múltiples outputs por base (igual que `final_paths += run_refine(...)` L457).
- El branch hace `sys.exit` temprano → el flujo de generación (L190-467) queda intocado.

#### 3. README.md — sección "Refinamiento" (L144-168)

Tras el bloque "Env del refino" (L157) y la nota "Fix de RAW" (L159), añadir:

```markdown
**Modo refine-only (`REFINE_ONLY=1`)**: refina bases YA generadas sin regenerar.
Pensado para el flujo del bot en 2 pasos (generar → confirmar → refinar la misma base).

```bash
# refinar 1+ bases existentes (paths CSV)
printf 'same subject, keep it photorealistic' | \
  REFINE_ONLY=1 REFINE_INPUT='/workspace/ComfyUI/output/a.png,/workspace/ComfyUI/output/b.png' \
  python3 /workspace/gen_comfy.py
```

Env: `REFINE_ONLY=1|true|yes`, `REFINE_INPUT=<paths CSV>`, y los mismos
`REFINE_FACES`/`REFINE_DENOISE`/`REFINE_STEPS`/`REFINE_CFG` del modo completo.

Exit codes: `0` salidas impresas (una por línea), `2` error de entrada (`REFINE_ONLY`
sin `REFINE_INPUT`), `3` sin salidas. Bases inexistentes se omiten con aviso en stderr;
si un refino individual falla, la base se conserva en la salida.
```

Actualizar también la línea "Env del refino" (L157) para incluir `REFINE_ONLY` / `REFINE_INPUT`.

## Context

- `@gen_comfy.py:2-13` docstring de cabecera (edit)
- `@gen_comfy.py:160-170` `run_refine` — firma kwargs; devuelve `[base_path]` si la base no existe (gotcha a evitar)
- `@gen_comfy.py:177-188` `main()` lee `prompt_text` + env de refino (insert branch tras L188)
- `@gen_comfy.py:194-196, 301-303, 306-308` patrón `sys.stderr.write` + `sys.exit(2)`
- `@gen_comfy.py:452-467` loop de refino + print + `sys.exit(0 if final_paths else 3)`
- `@README.md:144-168` sección "Refinamiento" (edit)
- `@AGENTS.md` REGLA #1 — no degradar sin consultar (leer antes de tocar el archivo)
- `@.grok/agent-memory/impact-analyzer/comfyui-refine-confirm-item1.md` impact cerrado

## Tasks

### Task 1: Harness `tests_refine_only.py` en ROJO

**type:** auto
**Objective:** El harness codifica los contratos del modo REFINE_ONLY y falla sobre el código actual (branch inexistente). `gen_comfy.py` NO se edita en esta task.
**Files:** `/home/ubuntu/comfyui-vast-setup/tests_refine_only.py` (create)
**Action:**

STRICT TDD. Escribir los 6 tests exactos (se pueden copiar literalmente del bloque "Exact implementation" de esta sección más abajo). Estructura del archivo:

- `import io, os, sys, pytest, gen_comfy`.
- Helpers: `_clean_env(monkeypatch, *, refine_only=None, refine_input=None, refine="1")` (delenv REFINE_ONLY/REFINE_INPUT/REFINE/MODEL; setenv MODEL=qwen y los dadas); `_run(monkeypatch, prompt=...)` (`monkeypatch.setattr(sys, "stdin", io.StringIO(prompt))` + `pytest.raises(SystemExit)` retorna `.value.code`); `_make_base(tmp_path, name)` (escribe un archivo temp y devuelve su str); `_refined(base)` → `"/workspace/ComfyUI/output/refined_" + os.path.basename(base)`.
- `import gen_comfy` funciona porque pytest inserta el dir del test en sys.path (no hay `__init__.py`); correr SIEMPRE desde `/home/ubuntu/comfyui-vast-setup`.

Tests:

| Test | Setup | Assert |
|------|-------|--------|
| `test_refine_only_refines_each_existing_base` | `REFINE_ONLY=1`, `REFINE_INPUT=a,b` (bases temp reales), `REFINE_DENOISE=0.3`, `REFINE_STEPS=22`, `REFINE_CFG=4.0`; monkeypatch `run_refine` → guarda `(base, prompt, kwargs)` y devuelve `[_refined(base)]` | exit 0; `run_refine` llamado con `[a,b]`; prompt `"make it prettier"`; kwargs `{"faces": True, "denoise": 0.3, "steps": 22, "cfg": 4.0}`; stdout == `[_refined(a), _refined(b)]` |
| `test_refine_only_skips_missing_base` | `REFINE_ONLY=1`, `REFINE_INPUT=a,gone` (a temp real, gone inexistente) | exit 0; `run_refine` llamado solo con `[a]`; stdout == `[_refined(a)]`; stderr contiene `gone` |
| `test_refine_only_all_missing_exits_3` | `REFINE_ONLY=1`, `REFINE_INPUT=gone` (inexistente); `run_refine` patcheado con `pytest.fail("run_refine no debe llamarse...")` | exit 3; stdout vacío; stderr contiene `gone` |
| `test_refine_only_without_input_exits_2` | `REFINE_ONLY=1`, sin `REFINE_INPUT` | exit 2; stdout vacío; stderr contiene `REFINE_INPUT` |
| `test_refine_only_keeps_base_on_refine_error` | `REFINE_ONLY=1`, `REFINE_INPUT=a,b`; `run_refine` raise `RuntimeError("boom")` para `a`, devuelve `[_refined(b)]` para `b` | exit 0; stdout == `[a, _refined(b)]` (base conservada); stderr contiene `boom` |
| `test_without_refine_only_keeps_refine1_flow` | `REFINE_ONLY` ausente, `REFINE_INPUT=ignored.png` (DEBE ignorarse), `REFINE=1`, `MODEL=krea2`, `LORA=krea_edit`, `INPUT_IMAGE=temp real`; monkeypatch `_run_graph` → `["/workspace/ComfyUI/output/krea2_krea_edit_00001_.png"]`; `run_refine` guarda llamadas y devuelve `[_refined(base)]` | exit 0; `run_refine` llamado con el path GENERADO (no `ignored.png`); stdout == `[_refined(generado)]` — **regression guard, esperado GREEN ya en código actual** |

Nota: `test_without_refine_only_keeps_refine1_flow` usa el camino identity-edit (grafo en Python puro, sin `/workspace/payloads/*.json`) para no depender de archivos del box; es la única vía que evita mockear `json.load`.

Correr y confirmar ROJO: `(a)`, `(b)`, `all_missing`, `(c)`, `(e)` fallan (sin el branch, `main()` cae a generación → `FileNotFoundError` por `/workspace/payloads/qwen.json`, o `SystemExit` inesperado); `(d)` pasa (regression guard de flujo original).

**Verification:**

```bash
cd /home/ubuntu/comfyui-vast-setup && /home/ubuntu/repos/grok/venv/bin/python -m pytest tests_refine_only.py -q
```

**Done:** Los 6 tests existen. Al menos `test_refine_only_refines_each_existing_base`, `test_refine_only_skips_missing_base`, `test_refine_only_all_missing_exits_3`, `test_refine_only_without_input_exits_2` y `test_refine_only_keeps_base_on_refine_error` fallan sobre el código actual. `test_without_refine_only_keeps_refine1_flow` pasa (guard).

### Task 2: Implementar branch REFINE_ONLY + docstring en `gen_comfy.py`

**type:** auto
**Objective:** El código de producción cumple los contratos del harness; suite verde.
**Files:** `/home/ubuntu/comfyui-vast-setup/gen_comfy.py` (docstring L2-13 + branch tras L188)
**Action:**

Implementar EXACTAMENTE como en "Architecture Approach § Exact implementation". NO:

- Tocar el flujo de generación (L190-467), el identity-edit, video, ni los valores por defecto de refino.
- Cambiar `run_refine` / `_run_graph` / `build_refine_graph`.
- Reordenar env ni `do_refine`.
- Añadir dependencias nuevas (solo stdlib).

Después, re-correr Task 1 hasta verde.

**Verification:**

```bash
cd /home/ubuntu/comfyui-vast-setup && /home/ubuntu/repos/grok/venv/bin/python -m pytest tests_refine_only.py -q
```

**Done:** Los 6 tests pasan. El branch está tras L188 y antes del bloque identity-edit; docstring actualizado. `python3 -c "import ast; ast.parse(open('gen_comfy.py').read())"` no reporta errores de sintaxis.

### Task 3: README + commit único

**type:** auto
**Objective:** Docs del setup alineadas al modo; todo el harness verde; un commit convencional en el repo setup.
**Files:** `/home/ubuntu/comfyui-vast-setup/README.md` (sección Refinamiento)
**Action:**

Añadir el bloque refine-only a README (L144-168) y actualizar la línea "Env del refino" (L157) con `REFINE_ONLY`/`REFINE_INPUT`. No tocar el resto del README.

Re-correr el harness completo. Commit único (work-unit) en `comfyui-vast-setup` con los 3 archivos (`gen_comfy.py`, `README.md`, `tests_refine_only.py`):

```bash
cd /home/ubuntu/comfyui-vast-setup
git add gen_comfy.py README.md tests_refine_only.py
git commit -m "feat(gen_comfy): add REFINE_ONLY mode to refine pre-generated bases"
```

Sin `Co-Authored-By`. Verificar con `git status` y `git log --oneline -1`.

**Verification:**

```bash
cd /home/ubuntu/comfyui-vast-setup && /home/ubuntu/repos/grok/venv/bin/python -m pytest tests_refine_only.py -q
git -C /home/ubuntu/comfyui-vast-setup status --short
git -C /home/ubuntu/comfyui-vast-setup log --oneline -1
```

**Done:** README documenta REFINE_ONLY/REFINE_INPUT/exit codes con ejemplo. Harness verde. Commit único con mensaje conventional; `git status` limpio (solo el commit nuevo).

## Instrucciones para gsd-executor

- **TDD order es obligatorio:** Task 1 (tests RED) → Task 2 (impl) → Task 3 (docs + commit). No implementar primero.
- **Repo y runner:** el cambio va en `/home/ubuntu/comfyui-vast-setup` (repo separado, con su propio git). Runner: `/home/ubuntu/repos/grok/venv/bin/python -m pytest` (pytest 8.4.2). El setup no tiene pytest.ini → defaults; tests síncronos. Correr SIEMPRE desde el dir del setup. NO correr tests del bot para este ítem (nada del bot cambia).
- **Leer AGENTS.md del setup ANTES de editar** — REGLA #1 "CALIDAD ANTES QUE FACILIDAD: no degradar sin consultar". El branch no puede degradar ningún flujo existente.
- **Work-unit commits** (`~/.claude/skills/work-unit-commits/SKILL.md`): un solo commit convencional al final incluyendo tests + impl + docs. Sin `Co-Authored-By`. No commit tests-only ni impl-only.
- **Patterns to copy:** `do_refine` parse L183-184; `sys.exit(2)` L194-196; try/except del refino L459-461; `sys.exit(0 if final_paths else 3)` L467. Copiar mecánicamente, no rediseñar `run_refine`.
- **Anti-patterns:**
  - Llamar `run_refine` sobre una base inexistente (devuelve `[base_path]` y la haría pasar por "refinada").
  - Meter el branch tras la generación o condicionarlo a `do_refine`.
  - Cambiar los defaults de refino (refine_steps=20, no el 52 de `run_refine`).
  - Tocar `bot.py` / `_comfyui_run_remote` / `_generate_comfyui` (ítem 2).
  - Mockear `os.path.exists` en el harness (usar archivos temp reales).
  - Añadir dependencias fuera de stdlib al setup.
- **Skills:** ninguno extra para el executor. No se requiere `telegram-bot-hardener` (no toca bot). La skill `work-unit-commits` aplica para el commit.
- **Verificación manual opcional (no bloqueante, requiere box):** desplegar `cp gen_comfy.py /workspace/gen_comfy.py` en el box y correr un refine-only real sobre una base existente — validación de humo, SOLO si el box está disponible; no es parte del DoD (el harness es la verificación).
- Si descubres gotchas no obvios, guárdalos en engram via `mem_save` con `project: 'grok'` y `topic_key: 'architecture/comfyui-refine-confirm'`.

## Test commands

Harness (primario):

```bash
cd /home/ubuntu/comfyui-vast-setup && /home/ubuntu/repos/grok/venv/bin/python -m pytest tests_refine_only.py -q
```

Syntax check:

```bash
cd /home/ubuntu/comfyui-vast-setup && /home/ubuntu/repos/grok/venv/bin/python -c "import ast; ast.parse(open('gen_comfy.py').read()); print('ok')"
```

## Risks + Mitigation

| Risk (from impact) | Mitigation | Where |
|--------------------|------------|-------|
| MEDIUM — Timeout SSH 600s (bot.py:3210) vs `_run_graph(timeout=1200)`: multi-path secuencial puede exceder 600s | **No resolver aquí** (ítem 2 del pool, bot). Documentado en Scope/Non-goals y README. | — |
| MEDIUM — `run_refine` devuelve `[base_path]` si la base no existe → base borrada pasaría como "refinada" | `os.path.exists(base)` ANTES de `run_refine`; skip + stderr + no añadir a salida | branch + `test_refine_only_skips_missing_base` / `all_missing` |
| LOW — Shell quoting de `REFINE_INPUT` | No resolver aquí; paths vienen del stdout del propio script; el bot hardeniza con regex en ítem 2 | — |
| MEDIUM — Degradación (REGLA #1 AGENTS.md): el branch roza el flujo actual | Branch solo se activa con `REFINE_ONLY`; flujo byte-idéntico si ausente; regression guard `test_without_refine_only_keeps_refine1_flow` | harness test (d) |
| MEDIUM — Un refino falla a mitad y pierde la base | try/except replica main() L459-461: stderr + conserva base en salida | branch + `test_refine_only_keeps_base_on_refine_error` |
| LOW — Sin limpieza de output entre runs SSH (los bases persisten) | Refino secuencial, sin race (impact OK). Fuera de scope | — |

## Success Criteria

- [ ] `REFINE_ONLY=1` + bases existentes → refina cada una, stdout con solo paths refinados, exit 0.
- [ ] `REFINE_ONLY=1` + base inexistente → no en stdout, `run_refine` no llamada para ella, stderr avisa.
- [ ] `REFINE_ONLY=1` sin `REFINE_INPUT` → exit 2, stderr con `REFINE_INPUT`.
- [ ] `REFINE_ONLY=1` + refino con excepción → base conservada en stdout, stderr `refino falló`.
- [ ] `REFINE_ONLY` ausente → flujo actual (REFINE=1, txt2img, img2img, identity, video) intacto; test (d) verde antes y después.
- [ ] Docstring (L2-13) y README documentan el modo con env, exit codes 0/2/3 y ejemplo.
- [ ] `tests_refine_only.py` corre SIN box (solo stdlib + monkeypatch); 6 tests verdes.
- [ ] Un commit convencional en `comfyui-vast-setup` (`feat(gen_comfy): add REFINE_ONLY mode...`), sin `Co-Authored-By`.
- [ ] No-touch intacto: `bot.py`, `payloads/`, `workflows/`, `nodes/`, `scripts/`, `setup.sh`.
