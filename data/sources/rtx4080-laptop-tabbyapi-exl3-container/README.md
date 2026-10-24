# RTX 4080 Laptop GPU TabbyAPI EXL3 container evidence

The unchanged registry lab (`lab/lab.py` at c0604cff48078bfc13678c42b501bdced0c0fb81, SHA256 b7d36ea47989bd9d5283d2d5799851827480aba53b11aacc9fe10dd403245cca) ran with `--on endpoint` against the owner's NVIDIA GeForce RTX 4080 Laptop GPU (12 GB, sm_89) on 2026-10-24 UTC. It used the existing frozen launch `tabbyapi-qwen3.5-9b-exl3-4bpw-128k`, with no changes. All six gates passed: load, chat, reasoning, tools, context and speed. The raw run JSON and the stock-written 128K recipe are copied byte for byte.

| Measurement | Actual result |
| --- | --- |
| Context configuration | 131,072 tokens (`max_seq_len` and `cache_size`), Q4 KV cache, one card, MTP draft |
| Context recall | 106,242 input tokens; correct code; 65.6 seconds |
| Streamed decode (lab speed gate) | 91.9 tokens/second |
| Complete uncapped streamed answer (lab speed gate) | 2,062 tokens in 22.6 seconds, natural `stop` |
| Independent repeat of the stock speed request | 107.7 tokens/second; 4,668 tokens in 41.5 seconds, natural `stop` |
| Lab-reported prefill | 1,620 tokens/second |
| Peak GPU memory during the run | 7,862 MiB of 12,282 MiB (52 MiB in use before launch) |
| Host MemAvailable low point / swap | 21.69 GB / 4.71 GB (4.66 GB at baseline) |

The card has more than 8 GB, so the registry's KV floor applies. The launch holds 131,072 tokens of KV, which meets the 131,072-token floor without an exemption.

## Every response ended naturally

The lab records `finish_reason` only for the chat answer and the tool follow-up. The lab was therefore pointed at a pass-through recorder on 127.0.0.1:15001, which forwarded each request unchanged to the server on 127.0.0.1:15000 and logged every response's finish reason. This recorder is a task tool, not part of the lab or the kit. Its log is [finish-recorder.jsonl](../../lab-runs/20261024-rtx-4080-laptop-12gb.finish-recorder.jsonl), and the checked postconditions are in [postconditions.json](../../lab-runs/20261024-rtx-4080-laptop-12gb.postconditions.json).

- The run made exactly seven `POST /v1/chat/completions` requests: the six gate requests plus the repeat. Every one returned HTTP 200 with no error.
- None of the requests set `max_tokens` or `max_completion_tokens`.
- Finish reasons: chat `stop`; reasoning `stop`; tool call `tool_calls` (with the `get_weather` call); tool result `stop`; context `stop`; speed stream `stop`; repeat stream `stop`.
- The [server log](../../lab-runs/20261024-rtx-4080-laptop-12gb.server.log) shows `max_tokens: 131046 (auto)` on the streams. That is TabbyAPI's own default (the context left after the prompt), not a cap set by the lab or the runner.

## Machine

- Lenovo laptop, Intel Core i9-14900HX (32 threads), 33.35 GB RAM, Linux 7.2.5, NVIDIA driver 610.57.04, PCIe gen 4 x16 ([kit probe](../../lab-runs/20261023-rtx-4080-laptop-12gb.probe.json); host RAM in the probe was taken while another job was running).
- Rootless Podman 6.1.1 with task-local storage. This host has no NVIDIA Container Toolkit or CDI spec, so the GPU reached the container by hand: `/dev/nvidia0`, `/dev/nvidiactl`, `/dev/nvidia-uvm` and `/dev/nvidia-uvm-tools` as devices, and the host driver's `libcuda`, `libnvidia-ml`, `libnvidia-ptxjitcompiler`, `libnvidia-nvvm` and `libnvidia-gpucomp` (610.57.04) bind-mounted read-only into `/usr/local/nvidia/lib64`, which is already on the image's `LD_LIBRARY_PATH`. On a host with the toolkit, `--gpus all` is the usual equivalent. That route was not measured here.
- The kit's `tools/memwatch.sh` needs `bc`, which this host lacks. Memory was therefore recorded once a second by a separately labelled native watcher with the same columns plus GPU utilisation, power and SM clock: [memwatch-native.csv](../../lab-runs/20261024-rtx-4080-laptop-12gb.memwatch-native.csv).
- No clock, power, fan or GPU-limit change; no output cap; no thinking budget; no sampler or seed changes.

## Exact inputs and evidence

- Image: ghcr.io/0xsero/tabbyapi-exl3@sha256:0f83e6198dc3be2561652d8df2525a7d1a69733e12fc8e25cbc5bc319a8a3ad1 (amd64 manifest sha256:9506a96c89fdd98b24db4cde9b5a6b390bf649af502ba234e17b41da0ed478bb, config sha256:938e1c1768292e15c03b651d29a1266be8396fb3c9db6f26a19fbdf789dba6fe).
- Weights: TheMelonGod/Qwen3.5-9B-exl3@e98453aa143a24fab9eb14b718daed02e2fa6eef (EXL3 4.0 bpw, 6-bit head). `model.safetensors` is 7,307,469,398 bytes with SHA256 c43536c08cb54147a097937868f0791f637f98d1505c79475aa9b9b05589a74b, and `tokenizer.json` has SHA256 5f9e4d4901a92b997e463c1f46055088b6cca5ca61a6522d1b9f64c4bb81cb42. All 13 files matched the Hub's LFS SHA256 or git blob ids before launch.
- Rendered launch SHA256: 72198e58f86afcf47cd0b7aae9d2c23dfc5f2af25440b260e2bca6a816e07097. Config file SHA256: d3efe710e28fe94a93a81a86bf52f74f95ab6740800890d78b6a4f9db7ee9d0c.
- [Raw lab run JSON](../../lab-runs/rtx-4080-laptop-12gb.qwen3.5-9b.tabbyapi-qwen3.5-9b-exl3-4bpw-128k.128k.20261024T000705.json); raw-file SHA256 72b7f651c70102c661f47c390de5220e507d4a722029d24b289d912bdf62ac23. Recipe log hash sha256:efc6b260bc176808 (the lab hashes the text before its final newline).
- [Speed repeat](../../lab-runs/20261024-rtx-4080-laptop-12gb.speed-repeat.json): one run of the lab's own `stream_rate` with the stock prompt, uncapped.
- The kit's published target and reference panel are for Qwen3.8-Flash-Next. Neither that panel nor the full kit speed sweep was run. This submission records the registry's six lab gates.

## Reproduction on a Linux host without the NVIDIA Container Toolkit

Use Python 3.11 or newer for the registry checks. Driver library names follow the installed driver version; adjust `DRIVER` and `LIBDIR` to match yours.

~~~bash
MODEL_DIR=/path/to/Qwen3.5-9B-EXL3-6hb-4bpw       # TheMelonGod/Qwen3.5-9B-exl3@e98453aa143a24fab9eb14b718daed02e2fa6eef
IMAGE=ghcr.io/0xsero/tabbyapi-exl3@sha256:0f83e6198dc3be2561652d8df2525a7d1a69733e12fc8e25cbc5bc319a8a3ad1
LIBDIR=/usr/lib DRIVER=610.57.04
STORE=$(mktemp -d /path/to/roomy-disk/sybil-4080l.XXXXXX) || exit 1
mkdir -p "$STORE/tmp" || exit 1
export TMPDIR="$STORE/tmp"
P=(podman --root "$STORE/root" --runroot "$STORE/run" --tmpdir "$STORE/tmp")
echo "c43536c08cb54147a097937868f0791f637f98d1505c79475aa9b9b05589a74b  $MODEL_DIR/model.safetensors" | sha256sum -c - || exit 1
python3 lab/lab.py render registry/recipes/nvidia/rtx-4080-laptop-12gb/qwen3.5-9b.tabbyapi.128k.json \
  | python3 -c 'import json,sys; sys.stdout.write(json.load(sys.stdin)["config"]["text"])' > "$STORE/config.yml" || exit 1
echo "d3efe710e28fe94a93a81a86bf52f74f95ab6740800890d78b6a4f9db7ee9d0c  $STORE/config.yml" | sha256sum -c - || exit 1
LIBS=()
for l in libcuda.so.1 libcuda.so libnvidia-ml.so.1 libnvidia-ptxjitcompiler.so.1 libnvidia-nvvm.so.4 libnvidia-gpucomp.so.$DRIVER; do
  f=$(readlink -f "$LIBDIR/$l"); [ -f "$f" ] || { echo "missing $l" >&2; exit 1; }
  LIBS+=(-v "$f:/usr/local/nvidia/lib64/$l:ro")
done
cleanup() {
  if [ -s "$STORE/container.cid" ]; then
    timeout 60 "${P[@]}" logs "$(cat "$STORE/container.cid")" > "$STORE/server.log" 2>&1 || true
    timeout 60 "${P[@]}" rm -f "$(cat "$STORE/container.cid")" >/dev/null || true
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
timeout 3000 "${P[@]}" pull "$IMAGE" || exit 1
timeout 120 "${P[@]}" run -d --cidfile "$STORE/container.cid" \
  --device /dev/nvidia0 --device /dev/nvidiactl --device /dev/nvidia-uvm --device /dev/nvidia-uvm-tools "${LIBS[@]}" \
  -e NVIDIA_VISIBLE_DEVICES=all --shm-size 8g -p 127.0.0.1:15000:5000 \
  -v "$MODEL_DIR":/workspace/models/Qwen3.5-9B-EXL3-6hb-4bpw:ro -v "$STORE/config.yml":/app/config.yml:ro \
  --entrypoint /opt/venv/bin/python3 "$IMAGE" main.py --config /app/config.yml || exit 1
READY=no
for i in $(seq 1 180); do
  timeout 5 curl --max-time 4 -sf http://127.0.0.1:15000/v1/models >/dev/null && { READY=yes; break; }
  sleep 5
done
[ "$READY" = yes ] || { echo 'server did not become healthy' >&2; exit 1; }
timeout 5400 python3 lab/lab.py try TheMelonGod/Qwen3.5-9B-exl3@e98453aa143a24fab9eb14b718daed02e2fa6eef \
  --model qwen3.5-9b --engine tabbyapi-qwen3.5-9b-exl3-4bpw-128k --card rtx-4080-laptop-12gb \
  --on endpoint --endpoint http://127.0.0.1:15000 --gpu 'NVIDIA GeForce RTX 4080 Laptop GPU' || exit 1
~~~

The example uses the measured image, weights, frozen config and the host GPU mapping. Its bounded lifecycle and isolated storage are reproduction helpers. It saves the server log before removing the container. The measured run also sent the lab's requests through the recorder described above, and the run required all seven chat completions to finish with `stop` (or `tool_calls` for the tool call) before it counted as qualified.
