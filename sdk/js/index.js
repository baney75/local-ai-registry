// Local AI registry, JavaScript. No dependencies; works in Node 18+, Deno, Bun and browsers.
//   const r = await pick({ gpu: "NVIDIA GeForce RTX 4090", vram: 24 });
//   console.log(command(r));
export const URL = "https://local.sybilsolutions.ai/api/v2/catalog.json";

let cached;
/** The whole catalog: cards (with their picks), recipes (with rendered launches), models. */
export async function catalog(url = URL) {
  return (cached ??= fetch(url).then((r) => { if (!r.ok) throw new Error(`catalog: ${r.status}`); return r.json(); }));
}

const norm = (s) => s.toLowerCase().replace(/\((r|tm)\)/g, "").replace(/geforce|nvidia|amd|radeon|intel|graphics|[^a-z0-9]/g, "");
const names = (id, c) => [norm(c.name), norm(id), ...(c.match?.names ?? [])];
/** The card id for a GPU as the driver names it (nvidia-smi, amd-smi, lspci) and its memory in GB. */
export function detect(cat, gpu, vram) {
  const n = norm(gpu);
  const hits = Object.entries(cat.cards).filter(([id, c]) => names(id, c).some((x) => x && n.includes(x)) && (!vram || Math.abs(c.vram_gb - vram) <= 2));
  const best = ([id, c]) => Math.max(...names(id, c).filter((x) => n.includes(x)).map((x) => x.length));
  return hits.sort((a, b) => best(b) - best(a) ||
    (vram ? Math.abs(a[1].vram_gb - vram) - Math.abs(b[1].vram_gb - vram) : 0))[0]?.[0] ?? null;
}

/** The recommended recipe for a GPU (or null); `all: true` returns the card's top three. */
export async function pick({ gpu, vram, all = false }, cat) {
  cat ??= await catalog();
  const id = detect(cat, gpu, vram);
  if (!id) return all ? [] : null;
  const rs = cat.cards[id].picks.map((k) => ({ key: k, ...cat.recipes[k] }));
  return all ? rs : rs[0];
}

const GPU = { cpu: "", nvidia: "--gpus all", "amd-rocm": "--device /dev/kfd --device /dev/dri", "amd-vulkan": "--device /dev/dri", "intel-xpu": "--device /dev/dri" };
const q = (s) => (/^[\w@%+=:,./-]+$/.test(s) ? s : `'${s.replace(/'/g, `'\\''`)}'`);
const value = (s) => /^\$\{[A-Z_][A-Z0-9_]*\}$/.test(s) ? `"${s}"` : q(s);
const files = (w) => (Array.isArray(w.files) ? w.files : w.files ? [w.files] : []).map((f) => ` ${q(f)}`).join("");
const weights = (l) => (Array.isArray(l.weights) ? l.weights : [l.weights]).filter((w) => w && w.repo);
const resources = (l) => {
  const r = l.resources ?? {};
  return [...(r.memoryBytes ? [`--memory ${r.memoryBytes}`] : []), ...(r.memorySwapBytes ? [`--memory-swap ${r.memorySwapBytes}`] : []),
    ...(r.memlockUnlimited ? ['--ulimit memlock=-1:-1'] : []), ...(r.ipcLock ? ['--cap-add IPC_LOCK'] : [])];
};

const pairs = (a) => {
  const args = [];
  for (let i = 0; i < a.length; i++) {
    const x = a[i], y = a[i + 1];
    if (x.startsWith("-") && y !== undefined && !y.startsWith("-")) { args.push(`${x} ${q(y)}`); i++; } else args.push(q(x));
  }
  return args;
};

// A pinned host runtime and Hugging Face cache weights: the same rules as lab/lab.py check_host_runtime and the Python SDK,
// checked again here so that nothing malformed is ever rendered into a shell command (fail closed).
const RT = {
  sha: /^[0-9a-f]{64}$/, rev: /^[0-9a-f]{40}$/,
  rel: /^(?!.*(?:^|\/)\.\.?(?:\/|$))[A-Za-z0-9_][A-Za-z0-9._+-]*(?:\/[A-Za-z0-9_][A-Za-z0-9._+-]*)*$/,
  url: /^https:\/\/[A-Za-z0-9.-]+(?:\/[A-Za-z0-9._~%+-]+)+$/,
  archive: /^[A-Za-z0-9][A-Za-z0-9._+-]*\.(?:tar\.gz|tgz)$/, wheel: /^[A-Za-z0-9][A-Za-z0-9._+-]*\.whl$/,
  name: /^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$/, version: /^[A-Za-z0-9][A-Za-z0-9.+!_-]*$/,
  ref: /^[A-Za-z0-9][A-Za-z0-9._-]*$/, repo: /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/,
};
const ok = (cond, msg) => { if (!cond) throw new Error(`invalid host runtime: ${msg}`); };
const is = (re, v) => typeof v === "string" && re.test(v);
const keys = (o) => (o && typeof o === "object" && !Array.isArray(o) ? Object.keys(o).sort().join(",") : null);
const canon = (n) => n.replace(/[-_.]+/g, "-").toLowerCase();
const urlFile = (u) => decodeURIComponent(u.slice(u.lastIndexOf("/") + 1));
const nested = (a, b) => a === b || a.startsWith(`${b}/`) || b.startsWith(`${a}/`);
function checkHost(l) {
  const ws = weights(l);
  ok(new Set(ws.map((w) => w.layout === "hub")).size <= 1, "hub and directory weights mixed in one launch");
  for (const w of ws) {
    if ("ref" in w) ok(w.layout === "hub" && is(RT.ref, w.ref), `weights.ref ${w.ref}`);
    if (w.layout === "hub") {
      ok(is(RT.repo, w.repo) && is(RT.rev, w.revision) && is(RT.rel, w.at), "hub weights repo, revision or cache dir");
      ok((Array.isArray(w.files) ? w.files : w.files ? [w.files] : []).every((f) => is(RT.rel, f)), "hub weights files");
    }
  }
  const rt = l.runtime;
  if (rt === undefined || rt === null) return checkPaths(l);
  ok(keys(rt) && Object.keys(rt).length && Object.keys(rt).every((k) => ["archive", "python", "packages"].includes(k)) && !l.pip?.length, "runtime keys");
  if ("archive" in rt) {
    const a = rt.archive;
    ok(keys(a) === "into,sha256,url", "runtime.archive keys");
    ok(is(RT.url, a.url) && is(RT.archive, urlFile(a.url)), `runtime.archive.url ${a.url}`);
    ok(is(RT.sha, a.sha256) && is(RT.rel, a.into), "runtime.archive sha256 or into");
  }
  if ("python" in rt) ok(is(RT.rel, rt.python) && "archive" in rt && rt.python.startsWith(`${rt.archive.into}/`), `runtime.python ${rt.python} is not inside runtime.archive.into`);
  if ("packages" in rt) {
    const pk = rt.packages;
    ok("python" in rt && keys(pk) === "dir,target,wheels", "runtime.packages keys");
    ok(is(RT.rel, pk.dir) && is(RT.rel, pk.target), "runtime.packages dir or target");
    ok(!nested(pk.dir, pk.target), "runtime.packages dir and target overlap");
    ok(Array.isArray(pk.wheels) && pk.wheels.length, "runtime.packages.wheels");
    const seen = new Set();
    for (const w of pk.wheels) {
      ok(keys(w) === "name,sha256,url,version", "wheel keys");
      ok(is(RT.name, w.name) && is(RT.version, w.version) && is(RT.url, w.url) && is(RT.sha, w.sha256), `wheel ${w.name}`);
      const f = urlFile(w.url), [dist = "", ver = ""] = f.split("-");
      ok(is(RT.wheel, f) && canon(dist) === canon(w.name) && ver === w.version, `wheel file ${f}`);
      ok(!seen.has(canon(w.name)), `wheel ${w.name} listed twice`);
      seen.add(canon(w.name));
    }
  }
  if (ws.length) ok("packages" in rt && rt.packages.wheels.some((w) => canon(w.name) === "huggingface-hub") && ws[0].layout === "hub",
    "weights with a runtime need hub layout and a pinned huggingface_hub wheel");
  if (l.config) ok(is(RT.rel, l.config.at), "launcher path");
  checkPaths(l);
}

/** What the setup steps publish besides the archive's entries: packages target and dir, host hub caches, launcher. */
function owned(l) {
  const pk = l.runtime?.packages, hub = weights(l).filter((w) => w.layout === "hub");
  return [...(pk ? [["packages.target", pk.target], ["packages.dir", pk.dir]] : []), ...hub.map((w) => ["weights.at", w.at]),
    ...(l.config && (l.runtime || hub.length) ? [["launcher", l.config.at]] : [])];
}
/** No step may publish over another's output: no two destinations equal or nest (hub caches may be shared), none is or
 *  holds runtime.archive.into, and none lies in the archive entry that holds the interpreter. */
function checkPaths(l) {
  const own = owned(l);
  own.forEach(([na, a], i) => own.slice(i + 1).forEach(([nb, b]) => ok((na === "weights.at" && nb === "weights.at") || !nested(a, b), `${na} ${a} and ${nb} ${b} overlap`)));
  const a = l.runtime?.archive;
  if (a) {
    const py = l.runtime.python, entry = py ? `${a.into}/${py.slice(a.into.length + 1).split("/")[0]}` : null;
    for (const [n, x] of own) {
      ok(!(x === a.into || a.into.startsWith(`${x}/`)), `${n} ${x} would replace runtime.archive.into`);
      ok(!entry || !nested(x, entry), `${n} ${x} overlaps the interpreter's archive entry ${entry}`);
    }
  }
}

const NOLINK = 'nolink() { for p; do if [ -L "$p" ]; then echo "local-ai: $p is a symlink; refusing to write through it" >&2; exit 1; fi; done; }';
const HUB = "import sys; sys.path.insert(0, sys.argv[1]); from huggingface_hub import snapshot_download; " +
  "snapshot_download(sys.argv[2], revision=sys.argv[3], cache_dir=sys.argv[4], allow_patterns=sys.argv[5:] or None)";
const KEEP = 'keep() { trap - EXIT; echo "local-ai: could not restore it; the previous copy is kept in $1" >&2; exit 1; }';
const CLEAN = `trap 'if [ -n "$t" ]; then rm -f "$t"; fi' EXIT`;
/** `a/b/c` -> `a a/b a/b/c`: every directory a write to `path` passes through, quoted. */
const prefixes = (path) => path.split("/").map((_, i, p) => q(p.slice(0, i + 1).join("/"))).join(" ");
const parent = (path) => (path.includes("/") ? path.replace(/\/[^/]*$/, "") : ".");
/** One step as one subshell that stops at its first failure and returns non-zero (pasted alone or in a script). */
const strict = (lines) => `(\nset -e\n${lines.join("\n")}\n)`;

/** The pinned interpreter archive, then the hash-locked offline wheel install. Each is staged in a fresh directory
 *  beside its destination and checked in full (hashes, entry names, links, the interpreter) before anything live is
 *  touched; it is then published by rename, and a failed rename puts back everything it had moved. */
function runtimeSteps(l) {
  const rt = l.runtime, out = [];
  if (rt.archive) {
    const a = rt.archive, into = a.into, f = urlFile(a.url), d = q(into);
    const keep = owned(l).map(([, x]) => x).filter((x) => x.startsWith(`${into}/`));
    out.push({ title: "Install the runtime", code: strict([
      NOLINK,
      `nolink ${prefixes(into)}`,
      `mkdir -p ${d}`,
      `s=$(mktemp -d ${d}/.local-ai-runtime.XXXXXX)`,
      `trap 'rm -rf "$s"' EXIT`,
      `curl -fL -o "$s"/${q(f)} ${q(a.url)}`,
      `(cd "$s" && echo '${a.sha256}  ${f}' | shasum -a 256 -c -)`,
      'mkdir "$s/x" "$s/old"',
      `tar -xzf "$s"/${q(f)} -C "$s/x"`,
      'test -n "$(ls -A "$s/x")"',
      'for n in $(ls -A "$s/x"); do',
      '  case $n in .local-ai-*|*[!A-Za-z0-9._+-]*) echo "local-ai: unexpected archive entry $n" >&2; exit 1;; esac',
      `  nolink ${d}/"$n"`,
      ...keep.map((x) => `  case ${q(`${x}/`)} in ${d}/"$n"/*) echo "local-ai: archive entry $n would replace ${x}" >&2; exit 1;; esac`),
      "done",
      ...(rt.python ? [`"$s"/x/${q(rt.python.slice(into.length + 1))} -I -c pass`] : []),
      KEEP,
      'undo() { echo "local-ai: could not publish the runtime; restoring the previous one" >&2; ' +
      `for n in $placed; do rm -rf ${d}/"$n"; done; for n in $(ls -A "$s/old"); do rm -rf ${d}/"$n"; mv "$s/old/$n" ${d}/"$n" || keep "$s/old"; done; exit 1; }`,
      "placed=",
      'for n in $(ls -A "$s/x"); do',
      `  if [ -e ${d}/"$n" ]; then mv ${d}/"$n" "$s/old/$n" || undo; fi`,
      `  mv "$s/x/$n" ${d}/"$n" || undo`,
      '  placed="$placed $n"',
      "done"]) });
  }
  if (rt.packages) {
    const pk = rt.packages, d = q(pk.dir), t = q(pk.target), fs = pk.wheels.map((w) => urlFile(w.url));
    out.push({ title: "Install the packages", code: strict([
      NOLINK,
      `nolink ${prefixes(pk.dir)} ${prefixes(pk.target)}`,
      `mkdir -p ${q(parent(pk.dir))} ${q(parent(pk.target))}`,
      `w=$(mktemp -d ${q(parent(pk.dir))}/.local-ai-wheels.XXXXXX)`,
      `o=$(mktemp -d ${q(parent(pk.dir))}/.local-ai-old.XXXXXX)`,
      `s=$(mktemp -d ${q(parent(pk.target))}/.local-ai-packages.XXXXXX)`,
      `trap 'rm -rf "$w" "$o" "$s"' EXIT`,
      ...pk.wheels.map((w, i) => `curl -fL -o "$w"/${q(fs[i])} ${q(w.url)}`),
      `printf '%s\\n' ${pk.wheels.map((w, i) => q(`${w.sha256}  ${fs[i]}`)).join(" ")} > "$w/SHA256SUMS"`,
      '(cd "$w" && shasum -a 256 -c SHA256SUMS)',
      `printf '%s\\n' ${pk.wheels.map((w) => q(`${w.name}==${w.version} --hash=sha256:${w.sha256}`)).join(" ")} > "$w/requirements.lock.txt"`,
      `${q(`./${rt.python}`)} -I -m pip --isolated install --no-index --find-links "$w" --only-binary :all: --require-hashes ` +
      '-r "$w/requirements.lock.txt" --target "$s/t" --no-compile --disable-pip-version-check',
      `nolink ${t} ${d}`,
      KEEP,
      'undo() { echo "local-ai: could not publish the packages; restoring the previous ones" >&2; ' +
      `if [ -e "$s/old" ]; then rm -rf ${t}; mv "$s/old" ${t} || keep "$s/old"; elif [ ! -e "$s/t" ]; then rm -rf ${t}; fi; ` +
      `if [ -e "$o/old" ]; then rm -rf ${d}; mv "$o/old" ${d} || keep "$o/old"; elif [ ! -e "$w" ]; then rm -rf ${d}; fi; exit 1; }`,
      `if [ -e ${t} ]; then mv ${t} "$s/old" || undo; fi`,
      `mv "$s/t" ${t} || undo`,
      `if [ -e ${d} ]; then mv ${d} "$o/old" || undo; fi`,
      `mv "$w" ${d} || undo`]) });
  }
  return out;
}

/** Write a file through an exclusive temp file in its (checked) directory, renamed over `path` only if `path` is
 *  neither a link nor a directory; the temp file is a fresh regular file that no one else could have created. */
const write = (path, body) => [`t=$(mktemp ${q(parent(path))}/.local-ai-tmp.XXXXXX)`, 'test -f "$t"', 'test ! -L "$t"', ...body('"$t"'),
  'chmod 644 "$t"', `nolink ${q(path)}`, `if [ -d ${q(path)} ]; then echo "local-ai: ${path} is a directory" >&2; exit 1; fi`, `mv -f "$t" ${q(path)}`];

/** Host weights: a directory (--local-dir), or a Hugging Face cache (layout hub) whose pinned snapshot must exist and,
 *  with `ref`, whose task-local refs/<ref> then names that revision. With a pinned runtime the download runs on its own
 *  interpreter and huggingface_hub, never an `hf` from PATH. */
function hostDownload(w, rt) {
  if (w.layout !== "hub") return `hf download ${w.repo}${files(w)} \\\n  --revision ${w.revision} \\\n  --local-dir ${w.at}`;
  const at = w.at, rev = w.revision, m = `${at}/models--${w.repo.replace("/", "--")}`;
  const own = [m, `${m}/snapshots`, `${m}/snapshots/${rev}`, `${m}/refs`, ...(w.ref ? [`${m}/refs/${w.ref}`] : [])].map(q).join(" ");
  const fs = Array.isArray(w.files) ? w.files : w.files ? [w.files] : [];
  const get = rt ? `${q(`./${rt.python}`)} -I -c ${q(HUB)} ${q(rt.packages.target)} ${w.repo} ${rev} ${q(at)}${fs.map((f) => ` ${q(f)}`).join("")}`
    : `hf download ${w.repo}${files(w)} --revision ${rev} --cache-dir ${q(at)}`;
  const lines = [`nolink ${prefixes(at)} ${own}`, `mkdir -p ${q(at)}`, get, `nolink ${own}`, `test -d ${q(`${m}/snapshots/${rev}`)}`];
  if (w.ref) lines.push(`mkdir -p ${q(`${m}/refs`)}`, `nolink ${q(`${m}/refs`)}`, ...write(`${m}/refs/${w.ref}`, (t) => [`printf '%s' ${rev} > ${t}`]));
  return lines;
}

/** A program on the host. One with pinned packages or weights (MLX, oMLX or llama.cpp on Apple silicon) also downloads
 *  its weights, writes its launcher and raises the GPU wired-memory limit before it starts. One with a pinned `runtime`
 *  installs that runtime first (in place of the free-text `install`). */
function hostSteps(l) {
  checkHost(l);
  const env = Object.entries(l.env ?? {}).map(([k, v]) => `${k}=${value(v)}`);
  const rt = l.runtime;
  let out;
  if (rt) out = runtimeSteps(l);
  else {
    if (!l.pip && !l.weights?.length) return [{ title: "Install", code: install(l.install) }, { title: "Start the server", code: [...env, ...l.command.map(q)].join(" ") }];
    out = [l.pip ? { title: "Install the engine", code: `python3 -m pip install ${l.pip.map(q).join(" ")}` } : { title: "Install the engine", code: install(l.install) }];
    if (l.pip && /^https:\/\/\S+\.tar\.gz#sha256=[0-9a-f]{64}$/.test(l.install ?? "")) out.push({ title: "Install the native engine", code: install(l.install) });
  }
  if (l.weights?.length) {
    if (l.weights.some((w) => w.layout === "hub")) out.push({ title: "Download the weights", code: strict([NOLINK, "t=", CLEAN, ...l.weights.flatMap((w) => hostDownload(w, rt))]) });
    else out.push({ title: "Download the weights", code: l.weights.map((w) => hostDownload(w)).join("\n\n") });
  }
  if (l.config) {
    const at = l.config.at;
    if (rt) out.push({ title: "Write the launcher", code: strict([NOLINK, "t=", CLEAN, `nolink ${prefixes(at)}`, `mkdir -p ${q(parent(at))}`,
      ...write(at, (t) => [`cat > ${t} <<'EOF'\n${l.config.text.trimEnd()}\nEOF`])]) });
    else out.push({ title: "Write the launcher", code: `${at.includes("/") ? `mkdir -p ${at.replace(/\/[^/]*$/, "")}\n` : ""}cat > ${at} <<'EOF'\n${l.config.text.trimEnd()}\nEOF` });
  }
  if (l.sysctl) out.push({ title: "Raise the GPU memory limit (resets on reboot)", code: Object.entries(l.sysctl).map(([k, v]) => `sudo sysctl ${k}=${v}`).join("\n") });
  const run = [...env, l.command[0], ...pairs(l.command.slice(1))];
  out.push({ title: "Start the server", code: run.filter(Boolean).join(" \\\n  ") });
  return out;
}

/** The steps to run a recipe by hand: download the weights, write the config, start the container. */
export function steps(r) {
  const l = r.launch, name = (r.key ?? r.model).split("/").pop();
  const src = l.source ? `\n# Full instructions: ${l.source}` : "";
  const out = l.setup ? [{ title: "Set up first", code: `# ${l.setup}${src}` }] : [];
  if (l.kind === "host") return [...out, ...hostSteps(l)];
  let image = l.image;
  if (l.build) {  // an image built from the source's Dockerfile at a pinned commit
    image = `local-ai/${name}`;
    const value = (s) => /^\$\{[A-Z_][A-Z0-9_]*\}$/.test(s) ? `"${s}"` : q(s);
const files = (l.build.dockerfile ?? "Dockerfile").split(" + ");
    out.push({ title: "Build the image", code: [install(`https://github.com/${l.build.repo}/tree/${l.build.commit}`), ...files.map((d) => `docker build -t ${image} -f ${d} .`)].join("\n") });
  }
  const prepared = `~/models/${name}-prepared-${image.slice(-12)}`;
  const mounts = [], dl = [];
  for (const w of weights(l)) {
    const dir = `~/models/${w.repo.split("/")[1]}-${w.revision.slice(0, 8)}`;
    if (w.layout === "hub") { dl.push(`hf download ${w.repo}${files(w)} \\\n  --revision ${w.revision}`); if (!mounts.includes("-v ~/.cache/huggingface:/root/.cache/huggingface")) mounts.push("-v ~/.cache/huggingface:/root/.cache/huggingface"); }  // several hub weights share one cache mount
    else { dl.push(`hf download ${w.repo}${files(w)} \\\n  --revision ${w.revision} \\\n  --local-dir ${dir}`); mounts.push(`-v ${dir}:${w.at}:ro`); }
  }
  if (dl.length) out.push({ title: "Download the weights", code: dl.join("\n\n") });
  if (l.config) {
    out.push({ title: "Write the server config", code: `cat > ${name}.yml <<'EOF'\n${l.config.text.trimEnd()}\nEOF` });
    mounts.push(`-v $PWD/${name}.yml:${l.config.at}:ro`);
  }
  const ep = Array.isArray(l.entrypoint) ? l.entrypoint : l.entrypoint ? [l.entrypoint] : [];
  const all = [...ep.slice(1), ...l.args], args = [];
  for (let i = 0; i < all.length; i++) {
    const a = all[i], b = all[i + 1];
    if (a.startsWith("-") && b !== undefined && !b.startsWith("-")) { args.push(`${a} ${value(b)}`); i++; } else args.push(value(a));
  }
  if (l.prepare) {
    const options = [...(l.flags ?? []), ...resources(l), ...(l.shm ? [`--shm-size ${l.shm}`] : []),
      ...Object.entries(l.env ?? {}).filter(([k]) => k !== 'NVIDIA_VISIBLE_DEVICES').map(([k, v]) => `-e ${k}=${value(v)}`), ...(ep.length ? [`--entrypoint ${q(ep[0])}`] : [])];
    const prep = ["docker run --rm", l.prepare.gpu === false ? "" : GPU[l.backend ?? "nvidia"] ?? GPU.nvidia, '--user "$(id -u):$(id -g)"', "-e HOME=/tmp",
      ...options, `-v ${prepared}:${l.prepare.at}`, ...mounts, image, ...[...ep.slice(1), ...l.prepare.args].map(q)];
    out.push({ title: "Prepare the model (first start)", code: `mkdir -p ${prepared}\n` + prep.filter(Boolean).join(" \\\n  ") });
    mounts.unshift(`-v ${prepared}:${l.prepare.at}:ro`);
    if (l.prepare.verifyArgs) out.push({ title: "Verify the prepared model", code: ['docker run --rm', '--user "$(id -u):$(id -g)"', '-e HOME=/tmp',
      ...options, ...mounts, image, ...[...ep.slice(1), ...l.prepare.verifyArgs].map(q)].join(' ') });
  }
  const run = ["docker run --rm", GPU[l.backend ?? "nvidia"] ?? GPU.nvidia, `-p 8000:${l.port}`, ...(l.flags ?? []), ...resources(l), ...(l.shm ? [`--shm-size ${l.shm}`] : []),
    ...Object.entries(l.env ?? {}).map(([k, v]) => `-e ${k}=${value(v)}`), ...mounts, ...(ep.length ? [`--entrypoint ${q(ep[0])}`] : []), image, ...args];
  out.push({ title: l.machines ? `Start the server on each of the ${l.machines} machines (NODE_RANK 0 to ${l.machines - 1})` : "Start the server", code: run.filter(Boolean).join(" \\\n  ") });
  return out;
}

/** Getting a source: a pinned GitHub tree becomes a clone at that commit. */
function install(url) {
  const m = url.match(/^https:\/\/github\.com\/([\w.-]+)\/([\w.-]+)\/tree\/([0-9a-f]{40})$/);
  if (m) return `git clone https://github.com/${m[1]}/${m[2]} && cd ${m[2]} && git checkout ${m[3]}`;
  const t = url.match(/^(https:\/\/\S+\/([\w.-]+\.tar\.gz))#sha256=([0-9a-f]{64})$/);  // a release archive, checked
  return t ? `curl -L -o ${t[2]} ${t[1]}\necho '${t[3]}  ${t[2]}' | shasum -a 256 -c\ntar xzf ${t[2]}` : `# ${url}`;
}

/** All the steps as one shell script. */
/** All the steps as one shell script. One with a pinned runtime or a host Hugging Face cache stops at the first step
 *  that fails, however it is run. */
export const command = (r) => {
  const l = r.launch, strictRun = l.kind === "host" && (l.runtime || weights(l).some((w) => w.layout === "hub"));
  return (strictRun ? "set -e\n\n" : "") + steps(r).map((s) => `# ${s.title}\n${s.code}`).join("\n\n");
};
