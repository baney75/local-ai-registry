# GTX 1650 SUPER CUDA12 container evidence

The unchanged current-main registry lab (`lab/lab.py` at c0604cff48078bfc13678c42b501bdced0c0fb81, SHA256 b7d36ea47989bd9d5283d2d5799851827480aba53b11aacc9fe10dd403245cca) ran against the owner's NVIDIA GeForce GTX 1650 SUPER (4 GB, sm_75) on 2026-10-23 UTC with `--on endpoint`. All six gates passed: load, chat, reasoning, tools, context and speed. The raw run JSON and the stock-generated 64K recipe are copied byte for byte.

| Measurement | Actual result |
| --- | --- |
| Context configuration | 65,536 tokens, one slot, f16 K/V cache, all layers on the GPU |
| Logical / physical batch | 512 / 128 tokens |
| Context recall | 45,462 input tokens; correct code; 192.9 seconds |
| Streamed decode (lab speed gate) | 85.4 tokens/second |
| Complete uncapped streamed answer | 2,013 tokens in 25.7 seconds, natural end |
| Lab-reported prefill | 236 tokens/second |
| Peak GPU memory during the run | 2,824 MiB of 4,096 MiB (167 MiB in use before launch) |
| Host MemAvailable low point / swap | 26.40 GB / 1.95 GB, unchanged from the baseline |

A 4 GB card is under the registry's 8 GB KV-floor threshold, so the 131,072-token floor does not apply. 128K was not attempted: at 16 KiB of f16 KV per token (8 attention layers x 8 KV heads x 64 dims), 128K needs about 2 GiB of KV on top of 1.48 GiB of weights, and llama.cpp reported 3,278 MiB free on the card.

## Machine

- WSL2 (kernel 6.6.87.2-microsoft-standard-WSL2) on a Windows host, NVIDIA driver 595.95, PCIe gen 3 x16, AMD Ryzen 5 3600, 29.43 GB RAM ([kit probe](../../lab-runs/20261023-gtx-1650-super-4gb.probe.json)).
- Rootless Podman 6.1.4. WSL has no NVIDIA Container Toolkit here, so the GPU reached the container through WSL's own paravirtual device: `--device /dev/dxg -v /usr/lib/wsl:/usr/lib/wsl:ro -e LD_LIBRARY_PATH=/usr/lib/wsl/lib`. On a Linux host with the toolkit, `--gpus all` is the equivalent; the image, argv and weights are the recipe's.
- The kit's `tools/memwatch.sh` needs `bc`, which this host lacks, so memory was recorded once a second by a separately labelled native watcher with the same columns plus GPU utilisation, power and SM clock: [memwatch-native.csv](../../lab-runs/20261023-gtx-1650-super-4gb.memwatch-native.csv).
- No clock, power or fan change; no output cap; no thinking budget.

## Exact inputs and evidence

- Image: ghcr.io/ggml-org/llama.cpp:server-cuda12-b11146@sha256:52192a23c7b258044463aebba78826cdff39a7393b713edc313324b343a7b836 (the GTX 1080 Ti recipe's image).
- Weights: LiquidAI/LFM2.5-2.6B-GGUF@84022ce711b28455e8c4fc364ce68c00cf995875; file LFM2.5-2.6B-QAD-Q4_0.gguf; 1,593,894,944 bytes; SHA256 a247afd6414918eac8e520a9e6137dc271235461ecbe1180462221d5b8d40b03.
- [Raw lab run JSON](../../lab-runs/gtx-1650-super-4gb.lfm2.5-2.6b.llama.cpp-lfm2.5-2.6b-qad-q4-0-64k-gtx1650super-cuda12.64k.20261023T195935.json); raw-file SHA256 055bb51a20461e0f74780c03b127eee5676ccef47fa0984b2ec6de90fb493f8f. Recipe log hash sha256:d8b66c6bb9c424e1 (the lab hashes the text before its final newline).
- Rendered launch SHA256: 636317017b0cee372cd35a07081b3f5550b68f0a183e29c66d5f2304eb831114.
- The kit reference panel and sweep apply only to Qwen3.8-Flash-Next and were not run; no panel result is claimed.

## Reproduction

~~~bash
MODEL_DIR=/path/to/LFM2.5-2.6B-GGUF-84022ce7
IMAGE=ghcr.io/ggml-org/llama.cpp:server-cuda12-b11146@sha256:52192a23c7b258044463aebba78826cdff39a7393b713edc313324b343a7b836
echo "a247afd6414918eac8e520a9e6137dc271235461ecbe1180462221d5b8d40b03  $MODEL_DIR/LFM2.5-2.6B-QAD-Q4_0.gguf" | sha256sum -c -
docker run -d --name lfm-1650 --gpus all -p 127.0.0.1:18080:8080 -v "$MODEL_DIR":/models:ro \
  --entrypoint /app/llama-server "$IMAGE" \
  -m /models/LFM2.5-2.6B-QAD-Q4_0.gguf --alias lfm2.5-2.6b --host 0.0.0.0 --port 8080 \
  --ctx-size 65536 --n-gpu-layers 999 --flash-attn auto --cache-type-k f16 --cache-type-v f16 \
  --jinja --reasoning-format deepseek --parallel 1 --batch-size 512 --ubatch-size 128
until curl -sf http://127.0.0.1:18080/health; do sleep 3; done
python3 lab/lab.py try LiquidAI/LFM2.5-2.6B-GGUF@84022ce711b28455e8c4fc364ce68c00cf995875 \
  --model lfm2.5-2.6b --engine llama.cpp-lfm2.5-2.6b-qad-q4-0-64k-gtx1650super-cuda12 \
  --card gtx-1650-super-4gb --on endpoint --endpoint http://127.0.0.1:18080 --gpu 'NVIDIA GeForce GTX 1650 SUPER'
~~~
