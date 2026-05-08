# Phase F — Step F9 smoke gate (2026-05-07) — **FAIL**

> Operator-run smoke gate for the Phase F toy action sprite pipeline. Per [phase-f-toy-action-sprites.md "Step F9"](../plan/phase-f-toy-action-sprites.md): "if any of the 8 criteria fails, file as a follow-up issue and fix before F10". This run failed criterion (1) "all 10 sprites generate without errors" because the backend process died via a native PyTorch crash mid-batch on the test toy. F10 is BLOCKED until the crash is root-caused.

## Executive answer

**FAIL.** Backend (`python.exe`) died via `c10.dll` access violation (`0xc0000005`) at fault offset `0x000000000007f804` while generating sprite 4-of-10 on a fresh toy. Three identical crashes captured in the Windows Application event log within ~36 minutes, all at the same fault offset — i.e. **deterministic, not random memory corruption**.

Of the 10 expected sprites for the smoke-test toy:
- 3 done (`idle`, `pointing`, `looking`) at the expected ~60-70 s/slot wall-clock
- 1 stranded `running` (`jumping` — the in-flight slot when the backend died)
- 6 stranded `queued` (`cheering`, `thinking`, `waving`, `running`, `sleeping`, `confused`)

The failure is in the v1 codebase, not the smoke procedure. The pipeline DOES work — an earlier toy on the same host completed all 10 slots — but the crash is reproducible enough that 7-of-10 stranded is the typical outcome on at-the-margin 8 GB hardware.

## Host

- **GPU**: NVIDIA GeForce RTX 4070 Laptop, 8 GB VRAM (compute capability 8.9), driver 581.95
- **PyTorch**: 2.6.0+cu124
- **CUDA runtime**: 12.4 (cuDNN 9.1.0)
- **OS**: Windows 11 Home
- **`.env` overrides**: `TOYBOX_IMAGE_GEN_MIN_VRAM_GB=6`, `TOYBOX_IMAGE_GEN_TIMEOUT_SEC=300`, `TOYBOX_IMAGE_GEN_BREAKER_THRESHOLD=5`
- **Capability gate at boot**: `capable=True reason=capable`

## Crash signature (3 identical occurrences)

Captured via `Get-WinEvent -LogName Application` (Provider=`Application Error`):

```
Faulting application name: python.exe (CPython 3.12, uv-managed)
Faulting module name:      c10.dll  (PyTorch 2.6.0 native runtime)
Exception code:            0xc0000005    (access violation)
Fault offset:              0x000000000007f804   (IDENTICAL across all 3 crashes)
Faulting module path:      C:\Users\abero\dev\toybox\.venv\Lib\site-packages\torch\lib\c10.dll
```

Timeline of crashes (PT → UTC for cross-ref to DB `updated_at`):

| Crash # | TimeCreated (PT) | UTC | Coincides with |
|---|---|---|---|
| 1 | 2026-05-07 22:34:08 | 05:34 Z | Mid-batch on toy `2e6931` (Plush Unicorn) — that toy nonetheless completed all 10 slots, suggesting a respawn or that an unrelated python process took the hit |
| 2 | 2026-05-07 22:43:14 | 05:43 Z | Same — toy `2e6931` was still running; later finished |
| 3 | 2026-05-07 23:10:05 | 06:10 Z | **Killed the smoke-test backend.** Toy `3413ff76` (Periodic Table of Elements) was at slot 4-of-10 (`jumping`) when the crash hit |

Same fault offset across 3 crashes = same code path. Probably the same line in `c10.dll` reachable from one of the SDXL/IP-Adapter/LoRA forward paths.

## Smoke-test toy timeline (Periodic Table of Elements, `3413ff76…`)

DB `toy_actions` snapshot, `updated_at` ascending:

| Slot | Status | Wall-clock from prev | Notes |
|------|--------|----------------------|-------|
| (10 enqueued) | queued | 06:05:39 Z | Toy commit hook fired; 10 slots queued |
| `idle` | done | +1 m 5 s (06:06:44 Z) | First-slot includes cold-start (~30 s extra) |
| `pointing` | done | +1 m 2 s (06:07:46 Z) | Steady-state |
| `looking` | done | +1 m 9 s (06:08:55 Z) | Steady-state |
| `jumping` | **running** | (06:08:55 Z) | In-flight when backend died |
| `cheering`, `thinking`, `waving`, `running`, `sleeping`, `confused` | queued | (06:05:39 Z) | Stranded; never started |

Steady-state ~60-70 s/sprite — about 2× slower than the [2026-05-06 8 GB feasibility probe's measured 30 s/sprite](2026-05-06-phase-f-8gb-feasibility.md). The slowdown is consistent with running the in-process pipeline alongside the production audio capture + whisper-on-CUDA pipeline contending for the same 8 GB GPU (the probe ran SDXL alone). Not the cause of the crash, but it does compress the wall-clock window in which the crash can happen.

## What worked (control evidence)

The pipeline itself is functional. Toy `2e6931` (an earlier Plush Unicorn upload at 05:28:23 Z, before this session's smoke test) completed **all 10 slots successfully** between 05:29:27 Z and 05:52:23 Z (~23 minutes total). So the crash is intermittent rather than blanket-broken — F9 step 1's "capable=True" determination was correct in principle, but the realized stability is below the 100% bar that smoke pass-criteria require.

## Earlier failure mode (different, also relevant)

Toy `f9cd3981` (an even earlier Plush Unicorn upload at 05:16:50 Z) failed with a *different* error class:
- `idle` and `pointing` slots: `error_msg='timeout'` (each ~2 minutes from queue) — this implies the run used the default `TOYBOX_IMAGE_GEN_TIMEOUT_SEC=120` rather than the `.env` override of 300, suggesting that backend instance was started before the `.env` change was applied
- `looking` slot: `error_msg='Expected all tensors to be on the same device, but found at least two devices, cuda:0 and cpu! (when checking argument for argument mat2 in method wrapper_CUDA_addmm)'`
- Remaining 7 slots: `error_msg='image-gen breaker open'` (mass-fail after threshold tripped)

This `cuda:0 vs cpu` mismatch is a Python-level exception (the worker caught it cleanly) but indicates a **device-placement bug in the pipeline** — somewhere a tensor stayed on CPU when it should have moved to CUDA. The same code path that produces that exception in Python could also produce a native crash if the bad pointer survives long enough to be dereferenced inside `c10.dll`. **Strong candidate root cause for the access-violation crashes.**

## What we did NOT verify (deferred)

| F9 criterion | Status | Reason |
|---|---|---|
| (1) all 10 sprites generate without errors | **FAIL** | 3-of-10 only |
| (2) WS-driven grid updates per slot | partial pass | grid live-updated through the 3 successful slots before backend died (visually confirmed by operator) |
| (3) per-slot wall-clock under 60 min budget | n/a | could not measure — backend died at slot 4 |
| (4) activity renders sprite per step in kiosk | not exercised | did not get to step F9.5/9.6; the backend died before any activity could be triggered |
| (5) regenerate one slot produces a different image (seed advance) | not exercised | did not get to step F9.7 |
| (6) restart-recovery sweep marks `running` rows `failed` with reason "interrupted by restart" | **deferred — see follow-up issue** | The backend died mid-flight without a clean shutdown; if a fresh boot is started against the current DB state it WILL exercise the sweep. We did not perform that boot in this session per operator decision to stop and triage. The DB still has slot `jumping` in `running` state — it's a captured fixture for the eventual recovery test |

## Suspect list (ranked)

1. **Latent device-placement bug in the pipeline** (HIGH). The cuda:0/cpu mismatch on `f9cd39` is the smoking gun. If a tensor's device pointer is occasionally wrong, a Python-level `addmm` will catch it, but a primitive op without that guard would dereference garbage and crash inside `c10.dll`. Same fault offset across 3 crashes is consistent with the same primitive op being reached. **First investigation target: audit `src/toybox/image_gen/pipeline.py` for `.to(device)` calls that could be skipped when a model/component is loaded later than expected (especially `image_encoder` and the LoRA path).** The Phase F doc explicitly flagged the gotcha that `image_encoder` must be passed at `from_pretrained` time — verify that requirement is enforced for ALL re-use of the pipeline across multiple slots, not just first construction.
2. **VRAM contention with whisper-on-CUDA** (MEDIUM). 8 GB total, SDXL peak ~6.1 GB (per the feasibility probe) + whisper-small ~250-500 MB + working set creep = ~1.4 GB headroom under steady state. A transient overshoot could cause the CUDA allocator to free + remap memory while a stale pointer is held in flight. Mitigation hypothesis: switch whisper to CPU (`TOYBOX_AUDIO_DEVICE=cpu` if it exists, else config) for the F10 soak.
3. **PyTorch 2.6.0 + CUDA 12.4 + RTX 4070 Laptop interaction** (LOW). 0xc0000005 in `c10.dll` is the right shape for a torch bug, but I have no specific known issue to point at. Worth a quick search of pytorch/pytorch issues for "c10.dll" + "0x7f804" + "RTX 4070" before committing engineering time elsewhere.
4. **`enable_model_cpu_offload()` race** (LOW). The probe's recommended config uses model_cpu_offload — that path moves parameters between GPU and CPU per-call, which is exactly the kind of motion that produces device-mismatch tensors if the offload state isn't perfectly synchronized with subsequent forwards. Worth checking whether `pipeline.py` releases all references between calls or if a stale tensor survives across slots.

## Recommended next steps

1. **File a GitHub issue** referencing this run-doc, with the c10.dll signature, fault offset, and the cuda:0/cpu device-mismatch error message verbatim. Attach the suspect list. (See "Filing details" below.)
2. **Audit `src/toybox/image_gen/pipeline.py`** for the device-placement bug per suspect #1. The most actionable lookups: `grep -n "\.to(" src/toybox/image_gen/pipeline.py` and verify every model-bearing object lands on `device='cuda'` before its first forward. Pay special attention to anything reused across `generate_action()` calls — the in-process worker holds a single pipeline reference and reuses it for all 10 slots.
3. **Try a one-toy reproduction with whisper-on-CPU** to isolate suspect #2. If the crash disappears with whisper on CPU, the root cause is VRAM contention and the fix is a config-time decision (declare image-gen and whisper mutually-exclusive on the GPU, or add a shared GPU mutex per the Phase F "VRAM contention with Phase E local LLM" risk row).
4. **Do NOT start F10** (30-toy soak) until criterion (1) reliably hits 10-of-10 across 3 consecutive single-toy runs. F10's pass criterion is "280+ of 300 jobs successful" — at the current ~30-70% per-toy success rate the soak would fail trivially and burn a 2.5h wall-clock window.

## Filing details (operator copy-paste)

```
Title: Phase F — c10.dll access violation crashes backend mid-sprite-batch
Body:
F9 smoke gate failed (see documentation/runs/2026-05-07-toy-action-sprites-smoke.md).

Backend python.exe dies via c10.dll access violation (Exception 0xc0000005, fault
offset 0x000000000007f804) during sustained SDXL+IP-Adapter+LoRA generation. Three
identical crashes captured in Windows Application event log on 2026-05-07. Same
fault offset across all 3 — deterministic, not random.

Suspect: latent CUDA device-placement bug in src/toybox/image_gen/pipeline.py
(corroborated by an earlier toy hitting `Expected all tensors to be on the same
device, but found at least two devices, cuda:0 and cpu!` on slot 3 before the
breaker tripped). The Python-level exception path catches it but a primitive op
without the guard could dereference garbage and crash native.

Host: RTX 4070 Laptop 8 GB, torch 2.6.0+cu124, CUDA 12.4, cuDNN 9.1.

Blocks: Phase F Step F10 (30-toy overnight soak).
```

## Database state at end of run (preserved as fixture for the recovery test)

Toys present:
- `3413ff76…` "Periodic Table of Elements" — 3 done, 1 running, 6 queued (the smoke-test toy; preserve as-is)
- `2e6931e0…` "Plush Unicorn" — 10 done (control: pipeline can succeed)
- `f9cd3981…` "Plush Unicorn" (archived) — 10 failed (cuda:0/cpu + breaker; control: device-mismatch evidence)
- `4070272…` "Pastel Unicorn Plush" (archived) — older test data; not relevant

The next backend boot SHOULD mark `3413ff76:jumping` `failed` with reason `"interrupted by restart"` per the Phase F restart-recovery sweep. That is the F9 step 8 verification and is the natural first thing to confirm after the c10.dll crash is fixed.
