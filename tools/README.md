# tools/

Codegen + automation entrypoints used by pre-commit hooks and CI.

## `gen_types_ts.py` — main entrypoint

Run from repo root:

```bash
python tools/gen_types_ts.py
```

Regenerates the TypeScript files under `frontend/src/shared/` from the
Python sources of truth in `src/toybox/`. The pre-commit hook
(`.pre-commit-config.yaml`) calls this before every commit and CI fails
if the output is dirty:

```bash
python tools/gen_types_ts.py
git diff --exit-code frontend/src/shared/errors.ts
```

## `gen_error_codes_ts.py` — deterministic StrEnum → TS fallback

Walks `toybox.core.errors.ErrorCode` and emits
`frontend/src/shared/errors.ts` directly. Used because the upstream
`pydantic2ts` package needs the `json2ts` Node CLI installed
(`npm install -g json-schema-to-typescript`) which is not yet a project
prerequisite.

When Pydantic models are added in Phase A Step 2+ that need TS shapes,
add per-module logic here OR install `json2ts` and re-enable the
pydantic2ts code path inside `gen_types_ts.py`.

## `spike_pydantic2ts.py` — one-time Phase A Step 1 spike

Diagnostic script that probes whether the local environment can run
`pydantic2ts` end-to-end. Kept in tree so future contributors can re-run
it after installing `json2ts` and decide whether to retire the fallback.

Phase A Step 1 spike outcome: **case 2** — `pydantic2ts` raised
`Exception: json2ts must be installed` because the Node CLI was not on
the PATH. The fallback `gen_error_codes_ts.py` was wired in.
