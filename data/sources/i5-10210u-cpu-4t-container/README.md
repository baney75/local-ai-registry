# Intel Core i5-10210U CPU container evidence (four threads)

The unchanged current-main registry lab (`lab/lab.py` at c0604cff48078bfc13678c42b501bdced0c0fb81, SHA256 b7d36ea47989bd9d5283d2d5799851827480aba53b11aacc9fe10dd403245cca) ran against the owner's Intel Core i5-10210U laptop CPU on 2026-10-23 UTC with `--on endpoint`. All six gates passed: load, chat, reasoning, tools, context and speed. The raw run JSON and the stock-generated recipe are copied byte for byte.

The launch is the existing `llama.cpp-lfm2.5-2.6b-qad-q4-0-8k-cpu-docker` profile (same image, weights, context and flags) with one change: `--threads 4 --threads-batch 4`, one thread per physical core, in place of six threads. The 4 GiB memory cap that the earlier CPU proofs stated in prose is now part of the launch as typed `resources`, so the recipe's launch hash binds it. The variant `4t` keeps the existing six-thread recipe on this card untouched.

| Measurement | Actual result |
| --- | --- |
| Context configuration | 8,192 tokens, one slot, f16 K/V cache, no GPU (`--n-gpu-layers 0`) |
| Threads / logical / physical batch | 4 (decode and batch) / 512 / 128 tokens |
| Context recall | 5,552 input tokens; correct code; 119.8 seconds |
| Streamed decode (lab speed gate, first 30 s) | 15.1 tokens/second |
| Complete uncapped streamed answer | 3,698 tokens in 257.4 seconds, natural end |
| Lab-reported prefill | 46 tokens/second |
| Independent repeat of the stock speed prompt | 15.13 tokens/second; 2,413 tokens in 164.8 seconds, natural end |
| Host MemAvailable start / low point; swap | 21.57 GB / 19.63 GB; 0.042 GB, unchanged from the baseline |

The pass is narrow: 15.1 against the lab's 15.0 floor. The gate measures the first 30 seconds. Over the whole answer, decode is lower as the context grows: the server log reports 14.40 tok/s for the gated answer and 14.65 tok/s for the repeat. These numbers belong to this CPU (4 cores, 8 threads, 15 W class, two DDR4 channels) and are not a speed claim for every AVX2 machine; the card is the registry's generic `x86-64-avx2-cpu`, and the proof's `gpu` field names the CPU actually measured.

## Machine

- Micro-Star (MSI) Modern 15 A10M laptop, Intel Core i5-10210U (4 cores, 8 threads, 1.6 GHz base, 4.2 GHz max), 23.3 GiB RAM, Linux 7.2.5. On AC power. Frequency governor `powersave` (the host's default); no clock, power, fan or OS change was made.
- Rootless Podman 6.1.1, container memory and memory+swap both capped at 4,294,967,296 bytes (`podman inspect`: `mem=4294967296 swap=4294967296`), bound to 127.0.0.1.
- The kit's `tools/memwatch.sh` was not used. Memory was recorded once a second by a separately labelled native watcher (MemAvailable and swap from `/proc/meminfo`; the GPU columns are empty because there is no GPU): [memwatch-native.csv](../../lab-runs/20261023-x86-64-avx2-cpu-i5-10210u.memwatch-native.csv).
- No output cap, no thinking budget, no forced stop. The lab's own requests set the sampling: temperature 0.6 for chat, reasoning, tools and context, and 0.8 for the speed prompt. These override the server's defaults. No `max_tokens` is sent.

## Exact inputs and evidence

- Image: ghcr.io/ggml-org/llama.cpp@sha256:23fd59bc5e5b06ca68003a5772d441f0411fb723eeaff850b9111bcd2ac4fd33 (the existing CPU profile's image, llama.cpp b11312).
- Weights: LiquidAI/LFM2.5-2.6B-GGUF@84022ce711b28455e8c4fc364ce68c00cf995875; file LFM2.5-2.6B-QAD-Q4_0.gguf; 1,593,894,944 bytes; SHA256 a247afd6414918eac8e520a9e6137dc271235461ecbe1180462221d5b8d40b03.
- Rendered launch SHA256 (lab `launch_hash`): f47439fecb04920684f9e6fa115763986025ee8ba8b2f54e739dd929fb9a49e4. The container was started from the lab's own `try --dry-run` rendering of this launch, not from hand-written flags.
- [Raw lab run JSON](../../lab-runs/x86-64-avx2-cpu.lfm2.5-2.6b.llama.cpp-lfm2.5-2.6b-qad-q4-0-8k-cpu-docker-4t.8k.20261023T220152.json); raw-file SHA256 5e9c7d07e01c93cf5e76f4f3fe2e4f28675b4ee13eee46f0ffbd32799e2d7935. Recipe log hash sha256:ca30b82cda779693 (the lab hashes the text before its final newline).
- [Full server log](../../lab-runs/x86-64-avx2-cpu.20261023T220152.i5-10210u-4t.server.log): seven completions (chat, reasoning, two tool turns, context, speed gate, repeat), each `stop processing … truncated = 0`.
- [Speed repeat](../../lab-runs/20261023-x86-64-avx2-cpu-i5-10210u.speed-repeat.json): `lab.stream_rate` imported from the unchanged `lab/lab.py`, with the speed prompt read from that file, against the same server after the gated run.
- The kit reference panel and full sweep apply only to Qwen3.8-Flash-Next, which this hardware cannot run. They were not run, and no panel result is claimed.

## Failed attempt, kept and excluded from this proof

The same machine ran the existing six-thread profile `llama.cpp-lfm2.5-2.6b-qad-q4-0-8k-cpu-docker` (launch SHA256 9da139208a0db2c91dd7ed83cf9a75bbd73ac05c3f09186e0babe0d4872b4b79) earlier on 2026-10-23 (run `x86-64-avx2-cpu.lfm2.5-2.6b.llama.cpp-lfm2.5-2.6b-qad-q4-0-8k-cpu-docker.8k.20261023T202345.json`). Five gates passed and speed failed at 14.6 tok/s (5,552-token recall in 139.1 s; 3,198-token answer in 228.4 s, natural end). The lab wrote no recipe. The run is kept in the owner's evidence, outside this change, and does not count toward this recipe.

## Reproduction

The commands check the weights hash, keep every log, stop only the container they start (by name), and exit non-zero when the lab fails.

~~~bash
set -euo pipefail
MODEL_DIR=/path/to/LFM2.5-2.6B-GGUF-84022ce7          # holds LFM2.5-2.6B-QAD-Q4_0.gguf
LOGS=./i5-cpu-4t-logs; mkdir -p "$LOGS"
NAME=lfm-cpu-4t
IMAGE=ghcr.io/ggml-org/llama.cpp@sha256:23fd59bc5e5b06ca68003a5772d441f0411fb723eeaff850b9111bcd2ac4fd33
echo "a247afd6414918eac8e520a9e6137dc271235461ecbe1180462221d5b8d40b03  $MODEL_DIR/LFM2.5-2.6B-QAD-Q4_0.gguf" | sha256sum -c -
echo "b7d36ea47989bd9d5283d2d5799851827480aba53b11aacc9fe10dd403245cca  lab/lab.py" | sha256sum -c -
python3 lab/lab.py try LiquidAI/LFM2.5-2.6B-GGUF@84022ce711b28455e8c4fc364ce68c00cf995875 --model lfm2.5-2.6b \
  --engine llama.cpp-lfm2.5-2.6b-qad-q4-0-8k-cpu-docker-4t --card x86-64-avx2-cpu --on endpoint --endpoint http://x \
  --dry-run > "$LOGS/dry-run.json"   # the rendered launch; its args are the ones below
cleanup() { docker logs "$NAME" > "$LOGS/server.log" 2>&1 || true; docker rm -f "$NAME" > /dev/null 2>&1 || true; }
trap cleanup EXIT
docker run -d --name "$NAME" --memory 4294967296 --memory-swap 4294967296 --shm-size 1g \
  -p 127.0.0.1:18090:8080 -v "$MODEL_DIR":/models:ro --entrypoint /app/llama-server "$IMAGE" \
  -m /models/LFM2.5-2.6B-QAD-Q4_0.gguf --alias LFM2.5-2.6B-GGUF --ctx-size 8192 --parallel 1 \
  --n-gpu-layers 0 --flash-attn off --no-mmproj --jinja --reasoning-format deepseek \
  --threads 4 --threads-batch 4 --batch-size 512 --ubatch-size 128 --host 0.0.0.0 --port 8080 --metrics
until curl -sf http://127.0.0.1:18090/health > /dev/null; do sleep 2; done
python3 lab/lab.py try LiquidAI/LFM2.5-2.6B-GGUF@84022ce711b28455e8c4fc364ce68c00cf995875 --model lfm2.5-2.6b \
  --engine llama.cpp-lfm2.5-2.6b-qad-q4-0-8k-cpu-docker-4t --card x86-64-avx2-cpu \
  --on endpoint --endpoint http://127.0.0.1:18090 --gpu "<your CPU>, four threads, 4 GiB container cap, no GPU" \
  2>&1 | tee "$LOGS/lab-try.log"   # exits 1 and writes no recipe if any gate fails; the run JSON stays in lab/runs/
~~~

`docker rm -f` removes the container and the llama-server process inside it. No other process is started. Rootless Podman takes the same flags. On a CPU with fewer than four physical cores, or one with less memory bandwidth, expect the speed gate to fail.
