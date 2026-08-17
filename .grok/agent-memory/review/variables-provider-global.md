# Review: variables-provider-global

**Effort:** 3 (1 general + tests + plan alignment)
**Rounds:** 2
**Exit:** 0 open issues (round 2)

## Round 1
- [General]: 0
- [Plan]: 0
- [Tests]: 3 suggestions (image_data on non-Kie batches; Kie reply `image_data=None`; real ComfyUI `wan_i2v` detector)

All three marked **fixed** in `c7c6640`.

## Round 2
- [General]: 0
- [Tests]: 0
- [Plan]: 0

## Totals
- bugs: 0
- suggestions: 3 (all fixed)
- nits: 0

## Residuals
- `_run_variables_batch` still >50 LOC — out-of-scope (A11)

## Commits
- `a22ddae` feat(variables): run batch on configured image provider
- `c7c6640` test(variables): lock i2i image_data and kie reply batch path
