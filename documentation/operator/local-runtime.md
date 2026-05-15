# Operator runbook: Phase E local-inference runtime

This runbook is the **E1a deliverable** for Phase E (local model + tool-loop).
It installs Ollama as the local-inference runtime, pulls both Qwen2.5 GGUFs
the plan calls for, verifies the runtime responds on the HTTP probe shape
the toybox capability gate expects, and documents the RunPod cloud-burst
escalation path.

Read end-to-end the first time you bring up local inference on a host;
subsequent installs can lean on the install + pull commands plus the smoke
check. See [`../plan/phase-e.md`](../plan/phase-e.md) for the full Phase E
context and how this step feeds E1b (smoke probe) and E1c (benchmark +
decision doc).

## Runtime choice + rationale

The Phase E plan lists three candidates: llama.cpp CUDA, Ollama, LM Studio.

| Runtime | Pick | Why |
|---|---|---|
| **Ollama** | ✅ canonical | Windows-native installer; REST API at `:11434` matches the `<TOYBOX_LOCAL_RUNTIME_URL>/v1/models` probe shape the `is_local_capable()` capability gate uses; uses llama.cpp under the hood so Ada-generation perf is identical to direct llama.cpp; supports custom GGUFs (Modelfile import) for E3's Unsloth-merged adapters; `OLLAMA_KV_CACHE_TYPE=q8_0` env knob halves KV-cache RAM — load-bearing on 8 GB hosts |
| llama.cpp direct | escape hatch | More control (GBNF grammars built-in for E2 constrained decoding), but only revisit if E2 finds Outlines too lossy. Compiling on Windows is non-trivial |
| LM Studio | skip | GUI-first; not scriptable for E1c's benchmark CLI; redundant with Ollama for what Phase E actually needs |

Default: **Ollama**. Everything below assumes this choice unless explicitly
flagged otherwise.

## Hardware floor

| | VRAM | What works | Notes |
|---|---|---|---|
| Comfortable | ≥12 GB | 7B Q4_K_M with full KV cache at 4 K context; coexists with image-gen | Mid-range desktop cards |
| **Constrained (Plan-default host)** | 8 GB | 7B Q4_K_M loads with `OLLAMA_KV_CACHE_TYPE=q8_0`; 3B Q5_K_M coexists with image-gen comfortably | RTX 4070 Laptop, what the plan was scoped against |
| Tight | 6 GB | 3B Q5_K_M only; 7B will OOM at 4 K context | Older laptop cards |
| CPU-only | n/a | 3B Q5_K_M at ~15–25 tok/s; 7B at ~3–6 tok/s — single-shot OK, loop mode too slow | Fallback if GPU unavailable |
| Cloud-burst | n/a | RunPod H100/A100, see Appendix B | Only when home GPU benchmark fails both 7B and 3B |

Confirm what the host has before starting:

```powershell
nvidia-smi --query-gpu=name,memory.total,driver_version,compute_cap --format=csv
```

You should see your GPU name, total VRAM in MiB, the driver version, and the
CUDA compute capability. CUDA 12.4+ runtime is fine; Ollama bundles its own
GPU runtime so you do not need a separate CUDA install.

## ⚠ Image-gen contention on 8 GB hosts

The Phase F.5 image-gen pipeline measured **6.11 GB peak VRAM** during the
F.5-2 probe ([feasibility report](../runs/2026-05-06-phase-f-8gb-feasibility.md)).
On an 8 GB host, this leaves ~2 GB of headroom — enough for 3B Q5_K_M
(~2.4 GB) but **NOT** enough for 7B Q4_K_M (~4.7 GB weights + ~1–2 GB KV cache).

Practical implications for an 8 GB host:

1. **Default to 3B Q5_K_M** as the production model. The plan's E1c gate
   chooses between 7B and 3B based on benchmark — on this host, 7B will
   benchmark fine in isolation but cannot run concurrently with image-gen.
   3B coexists.
2. **If E1c says "7B is the better model,"** the operational answer is
   either (a) sequentialize — image-gen worker quits when local-model loads
   and vice versa, capability gate enforces mutual exclusion — or (b)
   cloud-burst the 7B path (Appendix B).
3. **The image-gen capability gate already has a `MIN_VRAM_GB` env knob.**
   When the local-model runtime is actively loaded, image-gen will fail
   the VRAM check and route to Tier C composite, which is the desired
   degradation.

Record the chosen response in the E1c decision doc; the wiring lands in
Step 28 (E4 tool-loop)'s `is_local_capable()` implementation.

## Install Ollama

Run from any directory (this is system-level install):

```powershell
# winget; one-time, asks for elevation
winget install Ollama.Ollama --accept-source-agreements --accept-package-agreements
```

Open a **new** PowerShell window so the PATH update takes effect. Verify:

```powershell
ollama --version
```

You should see a version string like `ollama version is 0.5.x`.

## Configure persistent env vars

Ollama reads environment at startup. The two knobs that matter on an 8 GB
host are KV-cache quantization (halves cache RAM) and flash-attention
(reduces attention compute memory). Set them persistently so they survive
reboots:

```powershell
[Environment]::SetEnvironmentVariable("OLLAMA_KV_CACHE_TYPE", "q8_0", "User")
[Environment]::SetEnvironmentVariable("OLLAMA_FLASH_ATTENTION", "1", "User")
```

Open a new PowerShell window after setting these. Confirm:

```powershell
$env:OLLAMA_KV_CACHE_TYPE        # should print q8_0
$env:OLLAMA_FLASH_ATTENTION      # should print 1
```

## Start the Ollama server

```powershell
ollama serve
```

This binds `127.0.0.1:11434` and stays in the foreground. Leave the window
open — kill with `Ctrl-C` when you want to stop the runtime. For background
operation, install Ollama as a Windows service after E1a (out of scope for
this runbook; `New-Service` wrapping `ollama serve` is the standard
recipe).

In a second PowerShell window, confirm the server is up:

```powershell
curl http://127.0.0.1:11434/v1/models
```

You should get back JSON with `"data": []` (empty list — no models pulled
yet). If you get connection refused, `ollama serve` is not running in the
other window.

## Pull the GGUFs

The plan specifies two models: Qwen2.5-7B-Instruct Q4_K_M (primary) and
Qwen2.5-3B-Instruct Q5_K_M (fallback). Pull both:

```powershell
# ~4.7 GB
ollama pull qwen2.5:7b-instruct-q4_K_M

# ~2.4 GB
ollama pull qwen2.5:3b-instruct-q5_K_M
```

Total disk hit ≈ 7.1 GB. Ollama stores under `%USERPROFILE%\.ollama\models`
by default. If you want the storage on `data/models/` per the plan's
convention, set `OLLAMA_MODELS` to your preferred path **before** pulling:

```powershell
[Environment]::SetEnvironmentVariable("OLLAMA_MODELS", "C:\Users\abero\dev\toybox\data\models\ollama", "User")
```

(Restart `ollama serve` after changing this.)

Confirm both models registered:

```powershell
ollama list
```

You should see both tags with their digests and sizes.

## Smoke-load both models

Load each model in turn and confirm it responds without OOM. The first call
to a model triggers GPU load — it'll take 10–30 seconds the first time.

```powershell
ollama run qwen2.5:7b-instruct-q4_K_M "Say hello in one short sentence."
```

Wait for the response, then `Ctrl-D` to exit the interactive prompt.

```powershell
ollama run qwen2.5:3b-instruct-q5_K_M "Say hello in one short sentence."
```

Same drill. Both should print a short greeting without errors.

If 7B reports `cudaMalloc failed` or `out of memory`, the host can't hold
7B Q4_K_M concurrent with whatever else has VRAM committed. Free VRAM by
closing browser tabs / Discord / OBS / any other GPU-consuming app and
retry. If it still fails on a clean machine, the host can't run 7B with
the current settings — see Appendix B for cloud-burst, or stay on 3B-only.

## Sanity-check the capability-gate probe shape

`is_local_capable()` (lands in Step 28 / E4 carve-out) probes
`<TOYBOX_LOCAL_RUNTIME_URL>/v1/models` and looks for the chosen model id in
the response. Confirm both models are visible at that shape:

```powershell
curl http://127.0.0.1:11434/v1/models | ConvertFrom-Json | Select-Object -ExpandProperty data | Select-Object id
```

You should see both `qwen2.5:7b-instruct-q4_K_M` and
`qwen2.5:3b-instruct-q5_K_M` listed.

## Record model digests (sha256-equivalent)

The Phase E plan says "both GGUFs sha256-verified against upstream-published
checksums." Ollama doesn't surface upstream sha256s for pulled tags — it
fetches manifests + blobs from its own registry rather than HuggingFace
directly. The Ollama-native equivalent is the **manifest digest** per tag.

Record them now. `ollama show` does NOT print the digest in 0.23.3 (only
architecture, params, context length, quantization, license) — the digest
is exposed by the `/api/tags` HTTP endpoint:

```powershell
$ts = (Invoke-WebRequest -Uri "http://127.0.0.1:11434/api/tags" -UseBasicParsing).Content | ConvertFrom-Json
$ts.models | Format-List name, size, digest
```

Copy the digest line for each tag into the "Recorded on this host" section
near the bottom. This is the file integrity reference for future audits —
if a future `ollama pull` produces a different digest, you'll know the
upstream blob changed.

License caveat: `ollama show <tag>` does print the license block. As of
2026-05-14, **Qwen2.5-7B-Instruct is Apache 2.0 (fully permissive)**, but
**Qwen2.5-3B-Instruct ships under the Qwen RESEARCH LICENSE AGREEMENT
(non-commercial)**. For toybox's family-private personal use this is fine
on both sides, but if you ever want to ship the SFT-trained 3B adapter
publicly or commercially, you'll need to re-base on a permissively
licensed 3B (e.g. Llama-3.2-3B-Instruct under the Llama Community License,
or step up to Qwen2.5-7B which is Apache 2.0). Worth recording in the E1c
decision doc.

If you want the literal upstream HuggingFace sha256s as a stronger
cross-reference, the bundle files live at:
- [Qwen2.5-7B-Instruct-GGUF](https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF/blob/main/qwen2.5-7b-instruct-q4_k_m.gguf) — pull the SHA256 from the LFS pointer.
- [Qwen2.5-3B-Instruct-GGUF](https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/blob/main/qwen2.5-3b-instruct-q5_k_m.gguf) — same.

Copy those into "Recorded on this host" too if you want the upstream
cross-reference; Ollama's manifest digest alone is sufficient for the
build-step audit trail.

## Wire toybox env vars

Add these to your shell environment (or to a `.env` file you `source`
before launching the backend):

```powershell
# Which adapter to use; default = claude (existing v1 behavior)
[Environment]::SetEnvironmentVariable("TOYBOX_GENERATOR_ADAPTER", "claude", "User")

# Local runtime base URL — capability gate probes this
[Environment]::SetEnvironmentVariable("TOYBOX_LOCAL_RUNTIME_URL", "http://127.0.0.1:11434", "User")

# Chosen local model id; set after E1c picks 7B vs 3B
[Environment]::SetEnvironmentVariable("TOYBOX_LOCAL_MODEL_ID", "qwen2.5:3b-instruct-q5_K_M", "User")
```

`TOYBOX_GENERATOR_ADAPTER` stays at `claude` until E2 confirms the local
adapter is shippable. The other two are read by `is_local_capable()` once
it lands. Restart any open backend / shell after setting them.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `ollama` command not found after install | New PATH not loaded in current shell | Close + reopen PowerShell |
| `ollama serve` exits immediately, no error | Port 11434 already in use (another Ollama instance, or some other service) | `Get-NetTCPConnection -LocalPort 11434` to find the holder; kill it or set `OLLAMA_HOST=127.0.0.1:11435` |
| `ollama exited err="exit status 1"` repeating ~1×/s in the daemon log; `ollama run` hangs | Desktop tray app (`ollama app.exe`) and CLI `ollama serve` both fighting for port 11434 in a restart loop. Common right after install when winget auto-started the tray app | Kill everything and run `ollama serve` standalone: `Get-Process -Name "ollama*" \| Stop-Process -Force` then `ollama serve` (foreground) in a new PowerShell |
| `ollama run <model> "prompt"` blocks indefinitely on Windows | Tray app injects an interactive TTY shim that the non-TTY shell can't satisfy | Use the HTTP API instead: `POST http://127.0.0.1:11434/v1/chat/completions` with OpenAI-compat body. This is also what E1b's `--probe` CLI and E1c's benchmark will hit |
| `cudaMalloc failed` on 7B load | Concurrent GPU consumer (browser, image-gen) | Close GPU-consuming apps; retry. Persistent failure on clean machine → host can't run 7B, fall back to 3B |
| `curl http://127.0.0.1:11434/v1/models` returns connection refused | `ollama serve` not running | Start it in another window; leave foreground |
| First model invocation hangs >60 s | First-load GPU compile + weight upload | Normal on first run only. Subsequent runs are <2 s warm. If still hung after 5 min, kill + investigate |
| `ollama list` shows the model but `ollama run` says "model not found" | Tag typo (case matters: `q4_K_M` not `q4_k_m`) | Use exact tag from `ollama list` |
| Generated text is gibberish or stops mid-word | KV-cache q8_0 incompatibility with this model build | Unset `OLLAMA_KV_CACHE_TYPE` and retry; if that fixes it, file an issue against the model card |
| Backend log says "is_local_capable returned False, reason=local runtime not yet installed" | E4 carve-out stub (returns False until E1c lands) | Expected behavior pre-E1c; will resolve when the full `is_local_capable()` ships |

## Done-when checklist (per phase-e.md Step 25a)

- [ ] `ollama --version` prints a version
- [ ] `ollama serve` binds 127.0.0.1:11434 and stays up
- [ ] `curl http://127.0.0.1:11434/v1/models` returns valid JSON
- [ ] Both `qwen2.5:7b-instruct-q4_K_M` and `qwen2.5:3b-instruct-q5_K_M` listed in `ollama list`
- [ ] Both models load and respond to a one-prompt smoke without OOM
- [ ] Manifest digest of each model recorded below
- [ ] `TOYBOX_LOCAL_RUNTIME_URL`, `TOYBOX_LOCAL_MODEL_ID`, `TOYBOX_GENERATOR_ADAPTER` env vars set
- [ ] RunPod escalation procedure (Appendix B) read; API key sourcing decided if 8 GB host can't run 7B and 7B is needed

When all boxes are checked, E1a is done. Proceed to E1b (smoke probe via
`uv run python -m toybox.ai.local --probe` — autonomous build-step that
writes the `data/models/.probe-pass-<iso>.json` marker) and then E1c
(benchmark + decision doc).

## Recorded on this host

Fill in after install. This section is the operator-side audit trail —
attach to E1a's GitHub issue [#35](https://github.com/aberson/toybox/issues/35)
when closing the step.

- Install date: `2026-05-14`
- Host GPU: `NVIDIA GeForce RTX 4070 Laptop GPU`
- Host VRAM: `8188 MiB`
- Driver: `596.49` (CUDA compute capability `8.9`, Ada Lovelace)
- Ollama version: `0.23.3`
- 7B Q4_K_M manifest digest: `845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e` (size 4,683,087,332 bytes / 4.36 GiB)
- 3B Q5_K_M manifest digest: `19cf317bd4792942b759199b10725e223664a3bee93d223e44e085fabb278878` (size 2,224,824,432 bytes / 2.07 GiB)
- 7B smoke output: `"Hello!"` — cold-load + generate ~50 tokens in **12.7 s** via `POST /v1/chat/completions`. **VRAM after load: 6115 MiB / 8188 MiB** (~75 % used with no other GPU consumer running).
- 3B smoke output: `"Hello!"` — cold-load + generate in **43.6 s** (first-ever model load on host; subsequent loads of any model are faster once Ollama warms its GPU runtime). Second-load 7B at 12.7 s confirms this is a one-time per-host warm-up cost, not a per-model cost.
- Notes / deviations:
  - winget installer auto-started Ollama as a user-session app (`ollama app.exe` tray manager + `ollama` daemon). No manual `ollama serve` needed at first — but see next bullet.
  - The tray app and a separately launched `ollama serve` collide on port 11434 and put the daemon in a restart loop logging `ollama exited err="exit status 1"` every ~1 second; observed during this install. Resolution was `Stop-Process` all `ollama*` processes then start a single foreground `ollama serve`. Added as a troubleshooting row.
  - `OLLAMA_KV_CACHE_TYPE=q8_0` + `OLLAMA_FLASH_ATTENTION=1` set persistently at User scope **after** the auto-started daemon launched; took effect when the daemon was restarted post-cleanup. 7B at 6115 MiB VRAM is consistent with q8_0 KV cache being active (FP16 KV cache would land somewhat higher).
  - 7B smoke load took the GPU to 6115 MiB out of 8188 MiB. F.5 image-gen measured 6.11 GB peak. **6.1 + 6.1 = 12.2 > 8.2 GB**, so the contention analysis stands: 7B + image-gen cannot coexist on this host. Image-gen + 3B (~2.4 GB load) is comfortable. E1c's decision is now narrowed to "7B with sequentialization or cloud-burst" vs "3B + concurrent image-gen."
  - 3B is Qwen RESEARCH LICENSE (non-commercial); 7B is Apache 2.0. Recorded above in the "License caveat" subsection. No issue for personal use; flag if scope ever shifts.
  - 7B cold-load wall-clock of 12.7 s is well under the plan's E1c <30 s threshold even without warm-up. Informally suggests 7B is viable on perf grounds; the VRAM-contention question is the real gate on the chosen model.

---

## Appendix A: switching to llama.cpp direct

Only relevant if E2 finds Outlines-via-Ollama-OpenAI-compat too lossy for
constrained decoding. Skip unless that result comes back.

Install path:

```powershell
# Get a prebuilt CUDA llama.cpp release matched to your CUDA runtime
# (12.4 = CUDA 12.x release). Pick from:
#   https://github.com/ggerganov/llama.cpp/releases
# Look for: llama-bin-win-cuda-cu12.4-x64.zip (or current equivalent)
# Extract to e.g. C:\Tools\llama.cpp\
# Add C:\Tools\llama.cpp\ to user PATH
```

Then download GGUFs directly from HuggingFace (not via Ollama) to a known
path, e.g. `C:\Users\abero\dev\toybox\data\models\gguf\`, and serve:

```powershell
llama-server --host 127.0.0.1 --port 8080 `
  --model "C:\path\to\qwen2.5-7b-instruct-q4_k_m.gguf" `
  --ctx-size 4096 `
  --n-gpu-layers 999 `
  --grammar-file <path-to-gbnf-grammar>     # only if using GBNF constrained decoding
```

Update toybox env:

```powershell
[Environment]::SetEnvironmentVariable("TOYBOX_LOCAL_RUNTIME_URL", "http://127.0.0.1:8080", "User")
```

The `/v1/models` probe shape is identical (llama.cpp's server ships an
OpenAI-compatible API by default), so `is_local_capable()` works
unchanged. Trade-off: you give up Ollama's model-management abstraction
but gain native GBNF support and finer per-request inference knobs.

## Appendix B: RunPod cloud-burst escalation

The Phase E plan calls for documenting cloud-burst as the fallback when
the home GPU cannot run the chosen model. Two scenarios qualify:

1. **Both 7B and 3B benchmark below the E1c thresholds** on the home host
   — the formal trigger per the plan.
2. **E1c says 7B is the right model but the 8 GB host can't run it
   concurrent with image-gen** — the practical trigger for this specific
   host. Documented as a real escalation case for the Plan-default 4070
   Laptop.

### Account + API setup

1. **Create a RunPod account** at https://www.runpod.io/. Email
   verification, no credit card to start.
2. **Add payment.** RunPod bills pay-per-second of pod uptime. The
   relevant skus for Phase E:
   - **H100 80GB** — ~$2.5–4/hr on-demand; massive overkill for Qwen2.5-7B
     but the most reliable instance class.
   - **A100 80GB** — ~$1.5–2.5/hr; right-sized for 7B.
   - **A100 40GB** — ~$1–1.5/hr; fits 7B Q4_K_M comfortably and is the
     cost-optimal pick for short SFT training runs in Phase E3.
   - **RTX 4090 24GB** — ~$0.4–0.7/hr; fits 7B with room for image-gen,
     cheapest viable option for inference-only.
3. **Generate an API key** at Settings → API Keys. Store in your password
   manager — RunPod does not show it again after creation. Do NOT commit
   to git, do NOT put in any file under `data/` or repo root. If you want
   it in an env var, use User-scope, not project-scope:
   ```powershell
   [Environment]::SetEnvironmentVariable("RUNPOD_API_KEY", "<key>", "User")
   ```
   This survives reboots; new shells pick it up.
4. **Set a spending alert.** RunPod billing page → Spending limits → set
   a low daily cap (e.g. $20). Catches stuck pods that nobody killed.

### When to escalate

The plan's formal trigger: 7B Q4_K_M fails E1c benchmark **AND** 3B
Q5_K_M fails E1c benchmark on the home GPU. Practical extension for the
8 GB host: 7B is the better model per benchmark but cannot coexist with
image-gen and image-gen is non-negotiable for current operational use.

In both cases, write the cloud-burst decision into
`documentation/local-model-decision.md` as part of E1c, including:
- Which sku (H100 / A100-80 / A100-40 / 4090)
- Estimated cost per Phase E3 training run (Unsloth LoRA fine-tune of 7B
  on the redacted SFT corpus — typically 1–3 hours on an A100, so $2–8
  per iteration)
- Whether inference-time also runs on RunPod (continuous cost) or only
  training (one-shot cost per iteration)
- The home/cloud split: most likely "training on RunPod, inference on
  home GPU" — that's the privacy-preserving compromise (cloud only ever
  sees the redacted corpus, never user-runtime data)

### Spinning up a pod

1. RunPod → Pods → Deploy. Pick GPU sku from above.
2. Container image: `ollama/ollama:latest` for inference; `runpod/pytorch:2.4.0-py3.11-cuda12.4-devel-ubuntu22.04` for training.
3. Storage: 50 GB volume for the GGUFs + LoRA adapters.
4. Expose port 11434 (for Ollama HTTP) or use RunPod's web terminal for
   training runs.
5. **Always stop the pod manually when you're done.** RunPod bills
   per-second of uptime, not per-second of GPU use — an idle pod is still
   billing.

For training-only escalation, the `scripts/train_lora.py` driver shipped
in E3 (Step 27 #39) reads the corpus path + adapter output path from
env vars and runs the same Unsloth code locally or on RunPod
indifferently — copy the redacted corpus up, run, copy the adapter back
down.

### Privacy boundary

The redacted-for-SFT corpus is what goes to RunPod, never raw user data.
The PII-redaction pass (Step 27 / Phase E3 backend carve-out) is the
boundary. Verify the corpus file you upload contains no child names, no
addresses, no full-name patterns before pushing — the redaction runs
locally in `eval_dump.py --sft-export` mode and produces a redacted
JSONL. Sanity-check by `grep`ing for any known child names in the output
JSONL before scp/rsync to the pod.

## References

- [phase-e.md Step 25a](../plan/phase-e.md#step-25a-e1a--install-runtime--ggufs) — what this runbook delivers
- [phase-e.md Appendix § Adapter × mode matrix](../plan/phase-e.md#adapter--mode-matrix) — how `TOYBOX_GENERATOR_ADAPTER` + `TOYBOX_GENERATOR_MODE` compose
- [Phase F.5 8 GB feasibility report](../runs/2026-05-06-phase-f-8gb-feasibility.md) — measured 6.11 GB SDXL peak that drives the contention analysis
- [Ollama API docs](https://github.com/ollama/ollama/blob/main/docs/api.md) — OpenAI-compatible endpoints at `/v1/...`
- [Qwen2.5-7B-Instruct model card](https://huggingface.co/Qwen/Qwen2.5-7B-Instruct) — upstream
- [Qwen2.5-7B-Instruct-GGUF](https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF) — GGUF blob source (for direct llama.cpp path)
- [RunPod docs](https://docs.runpod.io/) — pod lifecycle, pricing
