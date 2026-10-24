#!/usr/bin/env python3
"""CPU-only fixtures for repro.sh with a fake runtime (Linux: repro.sh fences helpers through /proc).

  python3 data/sources/rtx4080-laptop-tabbyapi-exl3-container/test_repro.py

A stub `podman` and `nvidia-smi` go first on PATH; the stub's "container" is a fake TabbyAPI that answers
the unchanged stock lab (lab/lab.py from this checkout, run for real) correctly, or with a chosen fault.
Nothing touches a GPU, a real container or the network. Each test runs repro.sh in a scratch copy of the
checkout and checks its exit code, that its own container is positively gone, that a foreign container is
never touched, and that no helper or server process is left behind.
"""
import http.server, json, os, shutil, signal, socket, socketserver, subprocess, sys, tempfile, time, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
REL = os.path.relpath(HERE, ROOT)
SERVED = "Qwen3.5-9B-EXL3-6hb-4bpw"
DIGEST = "sha256:0f83e6198dc3be2561652d8df2525a7d1a69733e12fc8e25cbc5bc319a8a3ad1"


# ----------------------------------------------------------------------------- the fake TabbyAPI
def serve(port, faults):
    streams = [0]

    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format, *args):
            print("fake-server", format % args, flush=True)

        def send(self, code, body):
            b = json.dumps(body).encode()
            self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b)))
            self.end_headers(); self.wfile.write(b)

        def do_GET(self):
            if "never_healthy" in faults:
                return self.send(503, {"detail": "loading"})
            self.send(200, {"object": "list", "data": [{"id": SERVED}]})

        def do_POST(self):
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            if self.path == "/v1/token/encode":
                return self.send(200, {"length": max(1, len(req.get("text", "")) // 4)})
            msgs = req.get("messages") or []
            text = "\n".join(m.get("content") or "" for m in msgs)
            if req.get("stream"):
                streams[0] += 1
                which = "speed" if streams[0] == 1 else "repeat"
                self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.send_header("Connection", "close"); self.end_headers()
                for i in range(60):
                    d = {"choices": [{"index": 0, "delta": {"content": f"word{i} "}, "finish_reason": None}]}
                    self.wfile.write(b"data: " + json.dumps(d).encode() + b"\n\n"); self.wfile.flush(); time.sleep(0.005)
                fin = "length" if f"{which}_length" in faults else "stop"
                self.wfile.write(b"data: " + json.dumps({"choices": [{"index": 0, "delta": {}, "finish_reason": fin}]}).encode() + b"\n\ndata: [DONE]\n\n")
                self.close_connection = True
                return
            msg, kind = {"role": "assistant", "content": ""}, "chat"
            if req.get("tools") and msgs[-1]["role"] == "user":
                kind, msg["tool_calls"] = "tool", [{"id": "call_1", "type": "function", "index": 0,
                                                    "function": {"name": "get_weather", "arguments": "{\"city\": \"Paris\"}"}}]
            elif msgs[-1]["role"] == "tool":
                kind, msg["content"] = "toolresult", "The weather in Paris is 17°C and overcast."
            elif "17 * 23" in text:
                kind, msg["content"], msg["reasoning_content"] = "reasoning", "391", "17 times 23 is 391."
            elif "access code" in text:
                if "hang_context" in faults:
                    time.sleep(3600)
                kind, msg["content"] = "context", "58213"
            else:
                msg["content"] = "Red, Yellow, Blue"
            fin = "tool_calls" if kind == "tool" else "stop"
            if f"{kind}_length" in faults:
                fin = "length"
            self.send(200, {"choices": [{"index": 0, "message": msg, "finish_reason": fin}],
                            "usage": {"prompt_tokens": max(1, len(text) // 4), "completion_tokens": 5}})

    class S(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
    S(("127.0.0.1", port), H).serve_forever()


# ----------------------------------------------------------------------------- stub runtime
PODMAN = r'''#!/usr/bin/env bash
# fake podman. State: $FAKE_STATE/c/<id>/{labels,pid,name}. Every call is appended to $FAKE_STATE/calls.
S=$FAKE_STATE/c; mkdir -p "$S"
while [[ "${1:-}" == --root || "${1:-}" == --runroot || "${1:-}" == --tmpdir ]]; do shift 2; done
echo "$*" >> "$FAKE_STATE/calls"
label_of() { sed -n 's/^sybil.run=//p' "$S/$1/labels" 2>/dev/null; }
alive() { [ -f "$S/$1/pid" ] && kill -0 "$(cat "$S/$1/pid")" 2>/dev/null; }
stop() { [ -f "$S/$1/pid" ] && { kill -9 -- "-$(cat "$S/$1/pid")" 2>/dev/null; kill -9 "$(cat "$S/$1/pid")" 2>/dev/null; rm -f "$S/$1/pid"; }; }
case "$1" in
  image)
    case "$2" in exists) exit 0 ;; inspect) echo "$FAKE_DIGEST" ;; esac ;;
  pull) exit 0 ;;
  run)
    shift; cid=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'); labels=(); port=
    while [ $# -gt 0 ]; do
      case "$1" in
        --cidfile) cidfile=$2; shift 2 ;;
        --label) labels+=("$2"); shift 2 ;;
        --name) name=$2; shift 2 ;;
        -p) port=$(echo "$2" | cut -d: -f2); shift 2 ;;
        -v|-e|--device|--shm-size|--entrypoint) shift 2 ;;
        *) shift ;;
      esac
    done
    [ -n "${FAKE_RUN_FAIL:-}" ] && exit 125
    mkdir -p "$S/$cid"; printf '%s\n' "${labels[@]}" > "$S/$cid/labels"; echo "$name" > "$S/$cid/name"
    setsid "$FAKE_PY" "$FAKE_TEST" --serve "$port" "${FAKE_FAULTS:-}" > "$S/$cid/out.log" 2>&1 < /dev/null &
    echo $! > "$S/$cid/pid"
    echo "$cid" > "$cidfile"; echo "$cid" ;;
  inspect)
    if [ "$2" = --format ] || [ "$2" = -f ]; then fmt=$3; cid=$4; else fmt=; cid=$2; fi
    [ -d "$S/$cid" ] || exit 125
    case "$fmt" in
      *State.Running*) alive "$cid" && echo true || echo false ;;
      *Labels*) echo "$cid ${FAKE_LABEL_OVERRIDE:-$(label_of "$cid")}" ;;
      *) echo '[{"Id": "'"$cid"'"}]' ;;
    esac ;;
  logs)
    cid=${!#}; [ -d "$S/$cid" ] || exit 125
    case "${FAKE_LOGS:-ok}" in fail) exit 1 ;; empty) exit 0 ;; *) cat "$S/$cid/out.log"; echo "fake log end" ;; esac ;;
  kill) cid=${!#}; [ -d "$S/$cid" ] || exit 125; stop "$cid" ;;
  rm)
    cid=${!#}; [ -d "$S/$cid" ] || exit 1
    case "${FAKE_RM:-ok}" in
      always_fail) exit 2 ;;
      fail_once) [ -e "$FAKE_STATE/rm-failed" ] || { touch "$FAKE_STATE/rm-failed"; exit 2; } ;;
    esac
    stop "$cid"; rm -rf "$S/$cid" ;;
  container) [ -d "$S/${3}" ] && exit 0 || exit 1 ;;
  ps)
    want=$(echo "$*" | sed -n 's/.*label=sybil.run=\([^ ]*\).*/\1/p')
    for d in "$S"/*/; do [ -d "$d" ] || continue; c=$(basename "$d"); [ "$(label_of "$c")" = "$want" ] && echo "$c"; done; exit 0 ;;
esac
exit 0
'''

NVSMI = r'''#!/usr/bin/env bash
case "$*" in
  *query-compute-apps*) [ -n "${FAKE_GPU_APPS:-}" ] && echo "$FAKE_GPU_APPS"; exit 0 ;;
  "--query-gpu=name --format=csv,noheader") echo "${FAKE_GPU_NAME:-NVIDIA GeForce RTX 4080 Laptop GPU}" ;;
  "--query-gpu=driver_version --format=csv,noheader") echo 610.57.04 ;;
  *) echo "fake nvidia-smi $*" ;;
esac
'''


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


class Repro(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp(prefix="sybil-repro-test-")
        self.repo = os.path.join(self.t, "repo")
        for d in ("lab", "registry"):
            shutil.copytree(os.path.join(ROOT, d), os.path.join(self.repo, d), ignore=shutil.ignore_patterns("runs", "__pycache__"))
        os.makedirs(os.path.join(self.repo, "lab", "runs"))
        shutil.copytree(HERE, os.path.join(self.repo, REL), ignore=shutil.ignore_patterns("__pycache__"))
        # fake weights, and the copy's manifest replaced by theirs (the shipped manifest is checked in test_manifest)
        self.model = os.path.join(self.t, "model"); os.makedirs(self.model)
        import hashlib
        lines = []
        for name, data in (("config.json", b"{}"), ("model.safetensors", b"x" * 1000)):
            open(os.path.join(self.model, name), "wb").write(data)
            lines.append(f"sha256 {hashlib.sha256(data).hexdigest()} {len(data)} {name}")
        open(os.path.join(self.repo, REL, "weights.manifest"), "w").write("\n".join(lines) + "\n")
        self.libdir = os.path.join(self.t, "lib"); os.makedirs(self.libdir)
        for real, links in (("libcuda.so.610.57.04", ["libcuda.so.1", "libcuda.so"]), ("libnvidia-ml.so.610.57.04", ["libnvidia-ml.so.1"]),
                            ("libnvidia-ptxjitcompiler.so.610.57.04", ["libnvidia-ptxjitcompiler.so.1"]),
                            ("libnvidia-nvvm.so.610.57.04", ["libnvidia-nvvm.so.4"]), ("libnvidia-gpucomp.so.610.57.04", [])):
            open(os.path.join(self.libdir, real), "w").write("fake")
            for l in links:
                os.symlink(real, os.path.join(self.libdir, l))
        self.bin = os.path.join(self.t, "bin"); os.makedirs(self.bin)
        for name, text in (("podman", PODMAN), ("nvidia-smi", NVSMI)):
            p = os.path.join(self.bin, name); open(p, "w").write(text); os.chmod(p, 0o755)
        self.state = os.path.join(self.t, "state"); os.makedirs(os.path.join(self.state, "c"))
        self.store = os.path.join(self.t, "store")
        self.env = {**os.environ, "PATH": self.bin + ":" + os.environ["PATH"], "FAKE_STATE": self.state, "FAKE_PY": sys.executable,
                    "FAKE_TEST": os.path.join(self.repo, REL, "test_repro.py"), "FAKE_DIGEST": DIGEST, "MODEL_DIR": self.model, "STORE": self.store,
                    "LIBDIR": self.libdir, "SERVER_PORT": str(free_port()), "REC_PORT": str(free_port()), "PYTHON": sys.executable,
                    "HEALTH_TIMEOUT": "60", "LAB_TIMEOUT": "300", "REPEAT_TIMEOUT": "120", "REPRO_TIMEOUT": "600"}
        self.env.pop("SYBIL_REPRO_BOUNDED", None)
        self.foreign = "f" * 64  # someone else's container in the same store: must never be touched
        os.makedirs(os.path.join(self.state, "c", self.foreign))
        open(os.path.join(self.state, "c", self.foreign, "labels"), "w").write("sybil.run=SOMEONE-ELSE\n")

    def tearDown(self):
        for d in os.listdir(os.path.join(self.state, "c")):  # never leave a fake server behind, even on failure
            pid = os.path.join(self.state, "c", d, "pid")
            if os.path.exists(pid):
                try:
                    os.killpg(int(open(pid).read()), signal.SIGKILL)
                except Exception:
                    pass
        shutil.rmtree(self.t, ignore_errors=True)

    def run_repro(self, timeout=900, **env):
        r = subprocess.run(["bash", os.path.join(self.repo, REL, "repro.sh")], env={**self.env, **env}, capture_output=True, text=True, timeout=timeout)
        self.out = r.stdout + r.stderr
        return r.returncode

    def ours(self):
        return [d for d in os.listdir(os.path.join(self.state, "c")) if d != self.foreign]

    def calls(self):
        p = os.path.join(self.state, "calls")
        return open(p).read() if os.path.exists(p) else ""

    def run_dir(self):
        runs = sorted(d for d in os.listdir(self.store) if d.startswith("run-"))
        return os.path.join(self.store, runs[-1])

    def assertClean(self):
        self.assertEqual(self.ours(), [], "this run's container must be positively gone")
        self.assertTrue(os.path.isdir(os.path.join(self.state, "c", self.foreign)), "foreign container removed")
        self.assertNotIn(self.foreign, self.calls(), "foreign container was touched")
        left = subprocess.run(["pgrep", "-af", self.t], capture_output=True, text=True).stdout
        self.assertEqual([l for l in left.splitlines() if "pgrep" not in l], [], "helper or server process left behind")

    def test_qualified(self):
        rc = self.run_repro()
        self.assertEqual(rc, 0, self.out[-3000:])
        R = self.run_dir()
        pc = json.load(open(os.path.join(R, "postconditions.json")))
        self.assertTrue(pc["pass"]); self.assertEqual(len([c for c in pc["chat_records"] if "n" in c]), 7)
        self.assertTrue(os.path.getsize(os.path.join(R, "server.log")) > 0)
        self.assertEqual(open(os.path.join(R, "cleanup.ok")).read().strip(), "1")
        self.assertLess(self.calls().index("logs"), self.calls().rindex("rm "))  # log saved before removal
        self.assertClean()

    def assertGateTextRightButLength(self, fault):  # Sol r1 falsifier: correct text, finish length -> stock lab passes, repro fails
        rc = self.run_repro(FAKE_FAULTS=fault)
        self.assertEqual(rc, 8, self.out[-3000:])
        self.assertEqual(open(os.path.join(self.run_dir(), "lab.exit")).read().strip(), "0")  # the stock lab alone passes
        self.assertClean()

    def test_reasoning_length(self):
        self.assertGateTextRightButLength("reasoning_length")

    def test_tool_call_length(self):
        self.assertGateTextRightButLength("tool_length")

    def test_context_length(self):
        self.assertGateTextRightButLength("context_length")

    def test_speed_length(self):
        self.assertGateTextRightButLength("speed_length")

    def test_repeat_length(self):
        self.assertGateTextRightButLength("repeat_length")

    def test_log_failure_fails_run(self):
        rc = self.run_repro(FAKE_LOGS="fail")
        self.assertEqual(rc, 9, self.out[-2000:]); self.assertIn("rm ", self.calls())  # removal still attempted
        self.assertClean()

    def test_empty_log_fails_run(self):
        self.assertEqual(self.run_repro(FAKE_LOGS="empty"), 9, self.out[-2000:]); self.assertClean()

    def test_rm_fails_once_escalates(self):
        rc = self.run_repro(FAKE_RM="fail_once")
        self.assertEqual(rc, 0, self.out[-2000:]); self.assertIn("escalating", self.out); self.assertIn("kill ", self.calls())
        self.assertClean()

    def test_rm_always_fails(self):
        rc = self.run_repro(FAKE_RM="always_fail")
        self.assertEqual(rc, 9, self.out[-2000:]); self.assertIn("NOT removed", self.out)
        self.assertEqual(len(self.ours()), 1)  # still there, and the run says so

    def test_unowned_label_never_touched(self):
        rc = self.run_repro(FAKE_LABEL_OVERRIDE="SOMEONE-ELSE")
        self.assertEqual(rc, 5, self.out[-2000:])
        cid = self.ours()[0]
        after = self.calls().split("run -d", 1)[1]
        self.assertNotIn(f"logs --timestamps {cid}", after); self.assertNotIn(f"rm -f -t 20 {cid}", after); self.assertNotIn(f"kill {cid}", after)
        self.assertIn("left untouched", self.out)

    def test_gpu_busy(self):
        self.assertEqual(self.run_repro(FAKE_GPU_APPS="4242, python, 900 MiB"), 3); self.assertNotIn("run -d", self.calls()); self.assertClean()

    def test_wrong_gpu(self):
        self.assertEqual(self.run_repro(FAKE_GPU_NAME="NVIDIA GeForce RTX 4070"), 3); self.assertNotIn("run -d", self.calls())

    def test_missing_driver_library(self):
        os.remove(os.path.join(self.libdir, "libnvidia-gpucomp.so.610.57.04"))
        self.assertEqual(self.run_repro(), 4); self.assertNotIn("run -d", self.calls())

    def test_weights_mismatch(self):
        open(os.path.join(self.model, "config.json"), "w").write("{ }")
        self.assertEqual(self.run_repro(), 4); self.assertNotIn("run -d", self.calls())

    def test_lock_held(self):
        os.makedirs(self.store, exist_ok=True)
        holder = subprocess.Popen(["flock", os.path.join(self.store, "repro.lock"), "sleep", "60"])
        try:
            time.sleep(0.5)
            self.assertEqual(self.run_repro(), 7); self.assertNotIn("run -d", self.calls())
        finally:
            holder.kill(); holder.wait()

    def test_never_healthy_bounded(self):
        rc = self.run_repro(FAKE_FAULTS="never_healthy", HEALTH_TIMEOUT="8")
        self.assertEqual(rc, 6, self.out[-2000:]); self.assertClean()

    def test_lab_timeout_bounded(self):
        rc = self.run_repro(FAKE_FAULTS="hang_context", LAB_TIMEOUT="15")
        self.assertEqual(rc, 1, self.out[-2000:]); self.assertClean()

    def test_overall_timeout_cleans_up(self):
        rc = self.run_repro(FAKE_FAULTS="hang_context", REPRO_TIMEOUT="25")
        self.assertIn(rc, (124, 143), self.out[-2000:]); self.assertIn("cleanup", self.out); self.assertClean()

    def test_container_start_failure(self):
        self.assertEqual(self.run_repro(FAKE_RUN_FAIL="1"), 5); self.assertClean()


class Manifest(unittest.TestCase):
    def test_manifest_pins_the_measured_weights(self):
        rows = [l.split() for l in open(os.path.join(HERE, "weights.manifest"))]
        self.assertEqual(len(rows), 13)
        d = {r[3]: (r[0], r[1], int(r[2])) for r in rows}
        self.assertEqual(d["model.safetensors"], ("sha256", "c43536c08cb54147a097937868f0791f637f98d1505c79475aa9b9b05589a74b", 7307469398))
        self.assertEqual(d["tokenizer.json"][1], "5f9e4d4901a92b997e463c1f46055088b6cca5ca61a6522d1b9f64c4bb81cb42")

    def test_script_pins(self):
        s = open(os.path.join(HERE, "repro.sh")).read()
        for pin in ("tabbyapi-exl3@sha256:0f83e6198dc3be2561652d8df2525a7d1a69733e12fc8e25cbc5bc319a8a3ad1",
                    "Qwen3.5-9B-exl3@e98453aa143a24fab9eb14b718daed02e2fa6eef",
                    "72198e58f86afcf47cd0b7aae9d2c23dfc5f2af25440b260e2bca6a816e07097",
                    "d3efe710e28fe94a93a81a86bf52f74f95ab6740800890d78b6a4f9db7ee9d0c",
                    "b7d36ea47989bd9d5283d2d5799851827480aba53b11aacc9fe10dd403245cca"):
            self.assertIn(pin, s)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--serve":
        serve(int(sys.argv[2]), set(filter(None, (sys.argv[3] if len(sys.argv) > 3 else "").split(","))))
    else:
        unittest.main(verbosity=2)
