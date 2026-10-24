# Intel Core i5-10210U CPU container evidence (four threads)

> **CPU GGUF fallback proposal; exception to current no-GGUF wording requested; no compliance/acceptance claimed.**
> `docs/data.md` at c0604cff states "one rule: **no GGUF**" for new recipes. Its GGUF section names exceptions only for AMD; it says nothing about CPUs. The registry has no non-GGUF CPU engine (`registry/engines/` holds only NVIDIA images; every `backend: cpu` launch is llama.cpp). This recipe therefore follows the existing GGUF CPU recipe on this card, and is offered as a tested fallback for the maintainers to accept or decline. `lab/catalog.py` already ranks any GGUF recipe below non-GGUF ones on the same card, and this one also lists below the existing six-thread recipe.

The unchanged current-main registry lab (`lab/lab.py` at c0604cff48078bfc13678c42b501bdced0c0fb81, SHA256 b7d36ea47989bd9d5283d2d5799851827480aba53b11aacc9fe10dd403245cca) ran against the owner's Intel Core i5-10210U laptop CPU on 2026-10-23 UTC with `--on endpoint`. All six gates passed: load, chat, reasoning, tools, context and speed. The raw run JSON and the stock-generated recipe are copied byte for byte.

The launch is the existing `llama.cpp-lfm2.5-2.6b-qad-q4-0-8k-cpu-docker` profile (same image, weights, context and flags) with one change: `--threads 4 --threads-batch 4`, one thread per physical core, in place of six threads. The 4 GiB memory cap that the earlier CPU proofs stated in prose is now part of the launch as typed `resources`, so the recipe's launch hash binds it. The variant `4t` keeps the existing six-thread recipe on this card untouched.

| Measurement | Actual result |
| --- | --- |
| Context configuration | 8,192 tokens, one slot, f16 K/V cache, no GPU (`--n-gpu-layers 0`) |
| Threads / logical / physical batch | 4 (decode and batch) / 512 / 128 tokens |
| Context recall | 5,552 input tokens; correct code; 119.8 seconds |
| Streamed decode (lab speed gate, first 30 s) | 15.1 tokens/second |
| Complete uncapped streamed answer | 3,698 tokens in 257.4 seconds, ended before the context limit (see below) |
| Lab-reported prefill | 46 tokens/second |
| Independent repeat of the stock speed prompt | 15.13 tokens/second; 2,413 tokens in 164.8 seconds, ended before the context limit |
| Host MemAvailable start / low point; swap | 21.57 GB / 19.63 GB; 0.042 GB, unchanged from the baseline |

The pass is narrow, and nothing here guarantees it holds on another run or machine. The lab's speed gate measures only the first 30 seconds of the streamed answer: 15.1 tok/s in the gated run and 15.13 tok/s in the repeat, against a 15.0 floor. Over the whole answer, decode is below the floor, because it slows as the context grows. The server log reports 14.40 tok/s for the whole gated answer and 14.65 tok/s for the whole repeat. These numbers belong to this CPU (4 cores, 8 threads, 15 W class, two DDR4 channels) and are not a speed claim for every AVX2 machine; the card is the registry's generic `x86-64-avx2-cpu`, and the proof's `gpu` field names the CPU actually measured.

## Machine

- Micro-Star (MSI) Modern 15 A10M laptop, Intel Core i5-10210U (4 cores, 8 threads, 1.6 GHz base, 4.2 GHz max), 23.3 GiB RAM, Linux 7.2.5. On AC power. Frequency governor `powersave` (the host's default); no clock, power, fan or OS change was made.
- Rootless Podman 6.1.1, container memory and memory+swap both capped at 4,294,967,296 bytes (`podman inspect`: `mem=4294967296 swap=4294967296`), bound to 127.0.0.1.
- The kit's `tools/memwatch.sh` was not used. Memory was recorded once a second by a separately labelled native watcher (MemAvailable and swap from `/proc/meminfo`; the GPU columns are empty because there is no GPU): [memwatch-native.csv](../../lab-runs/20261023-x86-64-avx2-cpu-i5-10210u.memwatch-native.csv).
- No output cap, no thinking budget, no forced stop. The lab's own requests set the sampling: temperature 0.6 for chat, reasoning, tools and context, and 0.8 for the speed prompt. These override the server's defaults. No `max_tokens` is sent.
- CPU load from other work during the gated run was not recorded. Only the start-of-run load average (in the baseline) and memory were logged. The CPU's frequency under load was not recorded either.

## How the answers ended

The measured run had no relay in front of the server, so not every `finish_reason` was captured. What the evidence does show:

- The lab recorded `finish` `stop` for the chat answer and for the tool-result reply. The reasoning and tool gates passed on their content.
- The [server log](../../lab-runs/x86-64-avx2-cpu.20261023T220152.i5-10210u-4t.server.log) shows all seven requests ending with `truncated = 0`, at 122 to 5,683 tokens, all far below the 8,192 context:
  - chat;
  - reasoning;
  - both tool turns;
  - context;
  - the speed-gate story (3,723 tokens);
  - the repeat (2,438 tokens).

In this image's llama-server (llama.cpp source 0c1e5709), only these conditions end an answer without EOS:

- running out of context, which sets `truncated` when context shift is off, the default (`ctx_shift = false`) ([server-context.cpp#L1866-L1874](https://github.com/ggml-org/llama.cpp/blob/0c1e57098bba43ac29e6e3b677cdceebdd22334f/tools/server/server-context.cpp#L1866-L1874));
- `n_predict` ([#L1876-L1882](https://github.com/ggml-org/llama.cpp/blob/0c1e57098bba43ac29e6e3b677cdceebdd22334f/tools/server/server-context.cpp#L1876-L1882));
- `n_indent` and `t_max_predict_ms`.

The launch sets none of these and the lab sends none. Every other stop is EOS or a stop string ([#L1933-L1938](https://github.com/ggml-org/llama.cpp/blob/0c1e57098bba43ac29e6e3b677cdceebdd22334f/tools/server/server-context.cpp#L1933-L1938)), which the API reports as `stop` or `tool_calls` ([server-task.cpp#L414-L425](https://github.com/ggml-org/llama.cpp/blob/0c1e57098bba43ac29e6e3b677cdceebdd22334f/tools/server/server-task.cpp#L414-L425)). That every answer ended naturally is therefore an inference from the log and the source, not a captured `finish_reason`. `repro.sh` below captures and checks the `finish_reason` directly.


## Exact inputs and evidence

- Image: ghcr.io/ggml-org/llama.cpp@sha256:23fd59bc5e5b06ca68003a5772d441f0411fb723eeaff850b9111bcd2ac4fd33 (the existing CPU profile's image, llama.cpp b11312).
- Weights: LiquidAI/LFM2.5-2.6B-GGUF@84022ce711b28455e8c4fc364ce68c00cf995875; file LFM2.5-2.6B-QAD-Q4_0.gguf; 1,593,894,944 bytes; SHA256 a247afd6414918eac8e520a9e6137dc271235461ecbe1180462221d5b8d40b03.
- Rendered launch SHA256 (lab `launch_hash`): f47439fecb04920684f9e6fa115763986025ee8ba8b2f54e739dd929fb9a49e4. The container was started from the lab's own `try --dry-run` rendering of this launch, not from hand-written flags.
- [Raw lab run JSON](../../lab-runs/x86-64-avx2-cpu.lfm2.5-2.6b.llama.cpp-lfm2.5-2.6b-qad-q4-0-8k-cpu-docker-4t.8k.20261023T220152.json); raw-file SHA256 5e9c7d07e01c93cf5e76f4f3fe2e4f28675b4ee13eee46f0ffbd32799e2d7935. Recipe log hash sha256:ca30b82cda779693 (the lab hashes the text before its final newline).
- [Full server log](../../lab-runs/x86-64-avx2-cpu.20261023T220152.i5-10210u-4t.server.log): seven completions (chat, reasoning, two tool turns, context, speed gate, repeat), each `stop processing … truncated = 0`; see [How the answers ended](#how-the-answers-ended).
- [Speed repeat](../../lab-runs/20261023-x86-64-avx2-cpu-i5-10210u.speed-repeat.json): `lab.stream_rate` imported from the unchanged `lab/lab.py`, with the speed prompt read from that file, against the same server after the gated run.
- Scope of acceptance: this is a registry-lab acceptance only (`lab.py try --on endpoint`, the six gates, then `make check`). The kit's speed sweep (`tools/sweep.py`, the same protocol on every card) was **not run**. The kit's one published reference quality panel is for Qwen3.8-Flash-Next only, so no quality-panel result exists or is claimed for this model.

## Failed attempt, kept and excluded from this proof

The same machine ran the existing six-thread profile `llama.cpp-lfm2.5-2.6b-qad-q4-0-8k-cpu-docker` (launch SHA256 9da139208a0db2c91dd7ed83cf9a75bbd73ac05c3f09186e0babe0d4872b4b79) earlier on 2026-10-23 (run `x86-64-avx2-cpu.lfm2.5-2.6b.llama.cpp-lfm2.5-2.6b-qad-q4-0-8k-cpu-docker.8k.20261023T202345.json`). Five gates passed and speed failed at 14.6 tok/s (5,552-token recall in 139.1 s; 3,198-token answer in 228.4 s, natural end). The lab wrote no recipe. The run is kept in the owner's evidence, outside this change, and does not count toward this recipe.

## Reproduction

[`repro.sh`](repro.sh) runs the whole check under bounded steps and fails unless all of the following hold:

- the lab's six gates pass (`lab.py` exits 0);
- one repeat of the stock speed prompt reaches at least 15 tok/s, using the lab's own `stream_rate` and the prompt read from the unchanged `lab/lab.py`;
- exactly seven chat requests were sent, each ending on its own with one `finish_reason`: `tool_calls` for the tool gate's first request, `stop` for every other. None of them sent `max_tokens`, `max_completion_tokens`, `min_tokens`, `stop`, `stop_token_ids`, `ignore_eos` or any `*budget*` field;
- cleanup succeeded: the server log was saved (the command exited 0 and the file is non-empty), and the container was then removed and confirmed gone.

~~~bash
# once, bounded, outside the run's lifetime:
timeout --kill-after=60 1800 docker pull ghcr.io/ggml-org/llama.cpp@sha256:23fd59bc5e5b06ca68003a5772d441f0411fb723eeaff850b9111bcd2ac4fd33
# then, from a scratch copy of this registry checkout (the lab writes lab/runs/ and the recipe into it):
MODEL_DIR=/path/to/LFM2.5-2.6B-GGUF-84022ce7 LAB_DIR="$PWD" \
  GPU_NAME="<your CPU>, four threads, 4 GiB container cap, no GPU" \
  bash data/sources/i5-10210u-cpu-4t-container/repro.sh
# rootless Podman: RUNTIME=podman (or a wrapper that adds --root/--runroot for task-local storage)
~~~

How the script keeps the launch exact, and bounds and owns its work:

- **Launch exact.** Before anything starts, the script checks the GGUF and `lab/lab.py` against their SHA256. It then renders the launch with the lab itself (`lab.py try … --dry-run`) and refuses to continue unless the rendered `launch_sha256` is f47439fe…49e4. The container gets that rendering's image, entrypoint, argv, shm size and memory cap (`--memory` and `--memory-swap` 4,294,967,296), with the weights read-only at `/models`. The measured container's `podman inspect` showed the same digest, cap and argv.
- **Bounded.** The script re-executes itself under `timeout --kill-after=120 $LIFETIME` (default 3,600 s). The health wait stops at `HEALTH_SECS` (300); every `curl` has `-m 5`; the lab, the repeat and every runtime call have their own `timeout -k`. Long steps run as background children, so a TERM reaches the cleanup trap at once.
- **Cleanup budget.** The worst case after TERM is 92 s, which fits inside the 120 s before the outer KILL.
- **Helpers fenced.** The observer, the lab and the repeat each run under `setsid` as their own process group. Each is recorded by pid and start time, and signalled only while both still match: TERM, then KILL to the group, then reaped.
- **Owned.** Each run gets a fresh `mktemp -d` directory (mode 700). Its name is also the container name and the value of the label `io.local-ai-registry.repro`. Before logging or removing anything, the script checks that the cidfile holds a 64-hex id and that the container carries its own label. If either check fails, it touches nothing.
- **Logs first, removal always.** Cleanup saves the server log, then always attempts removal, even when the log failed. Removal counts as confirmed only when the runtime says the container does not exist. Cleanup succeeds only when both the log save and the removal succeeded.

[`observer.py`](observer.py) and [`check_run.py`](check_run.py) are task tools that sit outside the lab and do not change it. The observer copies bytes unchanged in both directions between the lab and the server and keeps a copy. The checker reads that copy afterwards: it parses each response as real HTTP (`http.client.HTTPResponse`, which removes chunked framing) and validates the JSON or SSE body, the choices, any `error` field, real tool calls, and `[DONE]`. Both files are byte-identical to the ones used with the GTX 1650 SUPER vLLM recipe (`data/sources/gtx1650super-vllm-autoround/`), where `repro.sh` passed end to end on real hardware. llama-server sends an empty-`choices` usage chunk only when a request asks for `stream_options.include_usage` ([server-task.cpp#L502-L513](https://github.com/ggml-org/llama.cpp/blob/0c1e57098bba43ac29e6e3b677cdceebdd22334f/tools/server/server-task.cpp#L502-L513)), and the lab never does, so the one-choice-per-event check holds.

What was tested: the measured proof above came from the same steps run by hand (the task's `chapel-run.sh`), not from `repro.sh`, and `repro.sh` has not been run with the real model on this CPU. It was exercised on Chapel1 with a fake runtime and a fake OpenAI server, with no model and no real container. All 30 checks passed:

- a clean pass, where the recorded `run` call carried exactly the measured argv, image, cap and shm;
- a `length` ending, rejected;
- the health timeout;
- the `LIFETIME` kill, with the container still removed;
- a foreign label, where nothing was touched;
- wrong weights, and a launch edited to six threads, both refused before start;
- an empty or failed log, where cleanup fails but the container is still removed;
- a hanging log under the lifetime limit;
- the worst case, with the log, `rm` and the existence check all hanging; it finished about 50 s after TERM, before the outer KILL;
- the pid fence.

On a CPU with fewer than four physical cores, or with less memory bandwidth, expect the speed gate to fail.
