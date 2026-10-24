"""Local AI registry, Python. Standard library only.

    from local_ai_registry import pick, command
    print(command(pick("NVIDIA GeForce RTX 4090", vram=24)))

    python3 local_ai_registry.py "RTX 4090" 24      # prints the command for your GPU
"""
import json
import re
import sys
import urllib.parse
import urllib.request

URL = "https://local.sybilsolutions.ai/api/v2/catalog.json"
_cache = {}


def catalog(url=URL):
    """The whole catalog: cards (with their picks), recipes (with rendered launches), models."""
    if url not in _cache:
        with urllib.request.urlopen(url, timeout=30) as r:
            _cache[url] = json.load(r)
    return _cache[url]


def _norm(s):
    s = re.sub(r"\((r|tm)\)", "", s.lower())
    return re.sub(r"geforce|nvidia|amd|radeon|intel|graphics|[^a-z0-9]", "", s)


def _names(i, c):
    return [_norm(c["name"]), _norm(i)] + list((c.get("match") or {}).get("names") or [])


def detect(cat, gpu, vram=None):
    """The card id for a GPU as the driver names it, and its memory in GB."""
    n = _norm(gpu)
    hits = [(i, c) for i, c in cat["cards"].items()
            if any(x and x in n for x in _names(i, c)) and (not vram or abs(c["vram_gb"] - vram) <= 2)]
    hits.sort(key=lambda x: (-max(len(y) for y in _names(*x) if y and y in n),
                             abs(x[1]["vram_gb"] - vram) if vram else 0))
    return hits[0][0] if hits else None


def pick(gpu, vram=None, all=False, cat=None):
    """The recommended recipe for a GPU (or None); all=True returns the card's top three."""
    cat = cat or catalog()
    i = detect(cat, gpu, vram)
    if not i:
        return [] if all else None
    rs = [{"key": k, **cat["recipes"][k]} for k in cat["cards"][i]["picks"]]
    return rs if all else rs[0]


GPU = {"cpu": "", "nvidia": "--gpus all", "amd-rocm": "--device /dev/kfd --device /dev/dri", "amd-vulkan": "--device /dev/dri", "intel-xpu": "--device /dev/dri"}


def _q(s):
    return s if re.fullmatch(r"[\w@%+=:,./-]+", s) else "'" + s.replace("'", "'\\''") + "'"  # the same quoting as the JS SDK


def _value(s):
    return '"' + s + '"' if re.fullmatch(r"\$\{[A-Z_][A-Z0-9_]*\}", s) else _q(s)


def _files(w):
    fs = w.get("files") or []
    return "".join(" " + _q(f) for f in (fs if isinstance(fs, list) else [fs]))


def _resources(l):
    r = l.get('resources') or {}
    return ([f"--memory {r['memoryBytes']}"] if r.get('memoryBytes') else []) + ([f"--memory-swap {r['memorySwapBytes']}"] if r.get('memorySwapBytes') else []) + \
        (['--ulimit memlock=-1:-1'] if r.get('memlockUnlimited') else []) + (['--cap-add IPC_LOCK'] if r.get('ipcLock') else [])


# A pinned host runtime and Hugging Face cache weights: the same rules as lab/lab.py check_host_runtime, checked again here so
# that nothing malformed is ever rendered into a shell command (fail closed).
_RT_SHA = r"[0-9a-f]{64}"
_RT_REL = r"(?!.*(?:^|/)\.\.?(?:/|$))[A-Za-z0-9_][A-Za-z0-9._+-]*(?:/[A-Za-z0-9_][A-Za-z0-9._+-]*)*"
_RT_URL = r"https://[A-Za-z0-9.-]+(?:/[A-Za-z0-9._~%+-]+)+"
_RT_ARCHIVE = r"[A-Za-z0-9][A-Za-z0-9._+-]*\.(?:tar\.gz|tgz)"
_RT_WHEEL = r"[A-Za-z0-9][A-Za-z0-9._+-]*\.whl"
_RT_NAME = r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?"
_RT_VERSION = r"[A-Za-z0-9][A-Za-z0-9.+!_-]*"
_RT_REF = r"[A-Za-z0-9][A-Za-z0-9._-]*"
_RT_REPO = r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"


def _ok(cond, msg):
    if not cond:
        raise ValueError(f"invalid host runtime: {msg}")


def _is(pattern, v):
    return isinstance(v, str) and re.fullmatch(pattern, v) is not None


def _canon(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def _url_file(url):
    return urllib.parse.unquote(url.rsplit("/", 1)[1])


def _check_host(l):
    """Refuse to render a host runtime or hub weights that lab.py check would reject."""
    for w in _weights(l):
        if "ref" in w:
            _ok(w.get("layout") == "hub" and _is(_RT_REF, w["ref"]), f"weights.ref {w.get('ref')!r}")
        if w.get("layout") == "hub":
            _ok(_is(_RT_REPO, w.get("repo")) and _is(r"[0-9a-f]{40}", w.get("revision")) and _is(_RT_REL, w.get("at")), "hub weights repo, revision or cache dir")
            fs = w.get("files") or []
            _ok(all(_is(_RT_REL, f) for f in (fs if isinstance(fs, list) else [fs])), "hub weights files")
    rt = l.get("runtime")
    if rt is None:
        return
    _ok(isinstance(rt, dict) and rt and set(rt) <= {"archive", "python", "packages"} and not l.get("pip"), "runtime keys")
    if "archive" in rt:
        a = rt["archive"]
        _ok(isinstance(a, dict) and set(a) == {"url", "sha256", "into"}, "runtime.archive keys")
        _ok(_is(_RT_URL, a["url"]) and _is(_RT_ARCHIVE, _url_file(a["url"])), f"runtime.archive.url {a.get('url')!r}")
        _ok(_is(_RT_SHA, a["sha256"]) and _is(_RT_REL, a["into"]), "runtime.archive sha256 or into")
    if "python" in rt:
        _ok(_is(_RT_REL, rt["python"]), f"runtime.python {rt.get('python')!r}")
    if "packages" in rt:
        pk = rt["packages"]
        _ok("python" in rt and isinstance(pk, dict) and set(pk) == {"dir", "target", "wheels"}, "runtime.packages keys")
        _ok(_is(_RT_REL, pk["dir"]) and _is(_RT_REL, pk["target"]), "runtime.packages dir or target")
        _ok(isinstance(pk["wheels"], list) and pk["wheels"], "runtime.packages.wheels")
        seen = set()
        for w in pk["wheels"]:
            _ok(isinstance(w, dict) and set(w) == {"name", "version", "url", "sha256"}, "wheel keys")
            _ok(_is(_RT_NAME, w["name"]) and _is(_RT_VERSION, w["version"]) and _is(_RT_URL, w["url"]) and _is(_RT_SHA, w["sha256"]), f"wheel {w.get('name')!r}")
            f = _url_file(w["url"])
            dist, ver = (f.split("-") + ["", ""])[:2]
            _ok(_is(_RT_WHEEL, f) and _canon(dist) == _canon(w["name"]) and ver == w["version"], f"wheel file {f!r}")
            _ok(_canon(w["name"]) not in seen, f"wheel {w['name']} listed twice")
            seen.add(_canon(w["name"]))


def _weights(l):
    ws = l.get("weights") or []
    return [w for w in (ws if isinstance(ws, list) else [ws]) if w and w.get("repo")]


def _runtime_steps(rt):
    """The pinned interpreter archive, then the hash-locked offline wheel install: each step stops at its first failure."""
    out = []
    if "archive" in rt:
        a = rt["archive"]
        f = _url_file(a["url"])
        out.append({"title": "Install the runtime", "code": " && \\\n".join([
            f"curl -fL -o {_q(f)} {_q(a['url'])}",
            f"echo '{a['sha256']}  {f}' | shasum -a 256 -c -",
            f"mkdir -p {_q(a['into'])}",
            f"tar -xzf {_q(f)} -C {_q(a['into'])}"])})
    if "packages" in rt:
        pk, d = rt["packages"], rt["packages"]["dir"]
        files = [_url_file(w["url"]) for w in pk["wheels"]]
        out.append({"title": "Install the packages", "code": " && \\\n".join([
            f"mkdir -p {_q(d)}",
            *[f"curl -fL -o {_q(d + '/' + f)} {_q(w['url'])}" for w, f in zip(pk["wheels"], files)],
            "printf '%s\\n' " + " ".join(_q(f"{w['sha256']}  {f}") for w, f in zip(pk["wheels"], files)) + f" > {_q(d + '/SHA256SUMS')}",
            f"(cd {_q(d)} && shasum -a 256 -c SHA256SUMS)",
            "printf '%s\\n' " + " ".join(_q(f"{w['name']}=={w['version']} --hash=sha256:{w['sha256']}") for w in pk["wheels"]) + f" > {_q(d + '/requirements.lock.txt')}",
            f"{_q(rt['python'])} -I -m pip install --no-index --find-links {_q(d)} --only-binary :all: --require-hashes "
            f"-r {_q(d + '/requirements.lock.txt')} --target {_q(pk['target'])} --no-compile --disable-pip-version-check"])})
    return out


def _host_download(w):
    """Host weights: a directory (--local-dir), or a Hugging Face cache (layout hub) whose pinned snapshot must exist and,
    with `ref`, whose task-local refs/<ref> then names that revision."""
    if w.get("layout") != "hub":
        return f"hf download {w['repo']}{_files(w)} \\\n  --revision {w['revision']} \\\n  --local-dir {w['at']}"
    d = f"{w['at']}/models--{w['repo'].replace('/', '--')}"
    parts = [f"hf download {w['repo']}{_files(w)} \\\n  --revision {w['revision']} \\\n  --cache-dir {_q(w['at'])}",
             f"test -d {_q(d + '/snapshots/' + w['revision'])}"]
    if w.get("ref"):
        parts += [f"mkdir -p {_q(d + '/refs')}", f"printf '%s' {w['revision']} > {_q(d + '/refs/' + w['ref'])}"]
    return " && \\\n".join(parts)


def _host_steps(l):
    """A program on the host. One with pinned packages or weights (MLX, oMLX or llama.cpp on Apple silicon) also downloads
    its weights, writes its launcher and raises the GPU wired-memory limit before it starts. One with a pinned `runtime`
    installs that runtime first (in place of the free-text `install`)."""
    _check_host(l)
    env = [f"{k}={_value(v)}" for k, v in (l.get("env") or {}).items()]
    if l.get("runtime"):
        out = _runtime_steps(l["runtime"])
    else:
        if not l.get("pip") and not l.get("weights"):
            return [{"title": "Install", "code": _install(l["install"])}, {"title": "Start the server", "code": " ".join(env + [_q(c) for c in l["command"]])}]
        out = [{"title": "Install the engine", "code": "python3 -m pip install " + " ".join(_q(x) for x in l["pip"]) if l.get("pip") else _install(l["install"])}]
        if l.get("pip") and re.fullmatch(r"https://\S+\.tar\.gz#sha256=[0-9a-f]{64}", l.get("install") or ""):
            out.append({"title": "Install the native engine", "code": _install(l["install"])})
    if l.get("weights"):
        out.append({"title": "Download the weights", "code": "\n\n".join(_host_download(w) for w in l["weights"])})
    if l.get("config"):
        at = l["config"]["at"]
        mk = f"mkdir -p {at.rsplit('/', 1)[0]}\n" if "/" in at else ""
        out.append({"title": "Write the launcher", "code": f"{mk}cat > {at} <<'EOF'\n{l['config']['text'].rstrip()}\nEOF"})
    if l.get("sysctl"):
        out.append({"title": "Raise the GPU memory limit (resets on reboot)",
                    "code": "\n".join(f"sudo sysctl {k}={v}" for k, v in l["sysctl"].items())})
    run = env + [l["command"][0]] + _pairs(l["command"][1:])
    out.append({"title": "Start the server", "code": " \\\n  ".join(run)})
    return out


def _pairs(a):
    """Arguments as `--flag value` pairs, one per line."""
    args, i = [], 0
    while i < len(a):
        if a[i].startswith("-") and i + 1 < len(a) and not a[i + 1].startswith("-"):
            args.append(f"{a[i]} {_q(a[i + 1])}"); i += 2
        else:
            args.append(_q(a[i])); i += 1
    return args


def steps(r):
    """The steps to run a recipe by hand: download the weights, write the config, start the container."""
    l = r["launch"]
    name = (r.get("key") or r["model"]).split("/")[-1]
    src = f"\n# Full instructions: {l['source']}" if l.get("source") else ""
    out = [{"title": "Set up first", "code": f"# {l['setup']}{src}"}] if l.get("setup") else []
    if l.get("kind") == "host":
        return out + _host_steps(l)
    image = l.get("image")
    if l.get("build"):  # an image built from the source's Dockerfile at a pinned commit
        image = f"local-ai/{name}"
        files = (l["build"].get("dockerfile") or "Dockerfile").split(" + ")
        out.append({"title": "Build the image", "code": "\n".join([_install(f"https://github.com/{l['build']['repo']}/tree/{l['build']['commit']}")] + [f"docker build -t {image} -f {d} ." for d in files])})
    ws = [w for w in (l["weights"] if isinstance(l["weights"], list) else [l["weights"]]) if w and w.get("repo")]
    prepared = f"~/models/{name}-prepared-{image[-12:]}"
    mounts, dl = [], []
    for w in ws:
        d = f"~/models/{w['repo'].split('/')[1]}-{w['revision'][:8]}"
        if w.get("layout") == "hub":
            dl.append(f"hf download {w['repo']}{_files(w)} \\\n  --revision {w['revision']}")
            if "-v ~/.cache/huggingface:/root/.cache/huggingface" not in mounts:  # several hub weights share one cache mount
                mounts.append("-v ~/.cache/huggingface:/root/.cache/huggingface")
        else:
            dl.append(f"hf download {w['repo']}{_files(w)} \\\n  --revision {w['revision']} \\\n  --local-dir {d}")
            mounts.append(f"-v {d}:{w['at']}:ro")
    if dl:
        out.append({"title": "Download the weights", "code": "\n\n".join(dl)})
    if l.get("config"):
        out.append({"title": "Write the server config", "code": f"cat > {name}.yml <<'EOF'\n{l['config']['text'].rstrip()}\nEOF"})
        mounts.append(f"-v $PWD/{name}.yml:{l['config']['at']}:ro")
    e = l.get("entrypoint")
    ep = e if isinstance(e, list) else [e] if e else []
    args, a = [], ep[1:] + l["args"]
    i = 0
    while i < len(a):
        if a[i].startswith("-") and i + 1 < len(a) and not a[i + 1].startswith("-"):
            args.append(f"{a[i]} {_value(a[i + 1])}"); i += 2
        else:
            args.append(_value(a[i])); i += 1
    if l.get("prepare"):
        options = list(l.get('flags') or []) + _resources(l) + ([f"--shm-size {l['shm']}"] if l.get('shm') else [])
        options += [f"-e {k}={_value(v)}" for k, v in (l.get('env') or {}).items() if k != 'NVIDIA_VISIBLE_DEVICES']
        options += [f"--entrypoint {_q(ep[0])}"] if ep else []
        prep = ["docker run --rm", "" if l['prepare'].get('gpu') is False else GPU.get(l.get("backend") or "nvidia", GPU["nvidia"]), '--user "$(id -u):$(id -g)"', "-e HOME=/tmp"]
        prep += options + [f"-v {prepared}:{l['prepare']['at']}"] + mounts + [image] + [_q(x) for x in ep[1:] + l["prepare"]["args"]]
        out.append({"title": "Prepare the model (first start)", "code": f"mkdir -p {prepared}\n" + " \\\n  ".join(x for x in prep if x)})
        mounts.insert(0, f"-v {prepared}:{l['prepare']['at']}:ro")
        if l['prepare'].get('verifyArgs'):
            out.append({'title': 'Verify the prepared model', 'code': ' '.join(['docker run --rm', '--user "$(id -u):$(id -g)"', '-e HOME=/tmp'] + options + mounts + [image] + [_q(x) for x in ep[1:] + l['prepare']['verifyArgs']])})
    run = ["docker run --rm", GPU.get(l.get("backend") or "nvidia", GPU["nvidia"]), f"-p 8000:{l['port']}"] + list(l.get("flags") or []) + _resources(l)
    run += [f"--shm-size {l['shm']}"] if l.get("shm") else []
    run += [f"-e {k}={_value(v)}" for k, v in (l.get("env") or {}).items()] + mounts
    run += [f"--entrypoint {_q(ep[0])}"] if ep else []
    m = l.get("machines")
    title = f"Start the server on each of the {m} machines (NODE_RANK 0 to {m - 1})" if m else "Start the server"
    out.append({"title": title, "code": " \\\n  ".join(x for x in run + [image] + args if x)})
    return out


def _install(url):
    """Getting a source: a pinned GitHub tree becomes a clone at that commit."""
    m = re.fullmatch(r"https://github\.com/([\w.-]+)/([\w.-]+)/tree/([0-9a-f]{40})", url)
    if m:
        return f"git clone https://github.com/{m[1]}/{m[2]} && cd {m[2]} && git checkout {m[3]}"
    t = re.fullmatch(r"(https://\S+/([\w.-]+\.tar\.gz))#sha256=([0-9a-f]{64})", url)  # a release archive, checked
    return f"curl -L -o {t[2]} {t[1]}\necho '{t[3]}  {t[2]}' | shasum -a 256 -c\ntar xzf {t[2]}" if t else f"# {url}"


def command(r):
    """All the steps as one shell script."""
    return "\n\n".join(f"# {s['title']}\n{s['code']}" for s in steps(r))


if __name__ == "__main__":
    r = pick(sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else None)
    print(command(r) if r else f"no tested recipe for {sys.argv[1]!r} yet: https://local.sybilsolutions.ai")
