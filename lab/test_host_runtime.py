"""The optional pinned host runtime (docs/design.md, "A pinned host runtime"): validation, pins, rendered steps, and the
steps themselves run in a sandbox with real shasum, tar, mktemp and pip and stand-ins for curl (no network). The whole
one-script command from both SDKs is run under bash, bash -e and sh."""
import base64
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import lab

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'sdk' / 'python'))
import local_ai_registry as sdk  # noqa: E402

REV = '0123456789abcdef0123456789abcdef01234567'
ARCHIVE = 'cpython-3.12.15+20261021-aarch64-apple-darwin-install_only.tar.gz'
ARCHIVE_URL = 'https://github.com/astral-sh/python-build-standalone/releases/download/20261021/' + ARCHIVE.replace('+', '%2B')
WHEEL_URL = 'https://files.example.invalid/packages/'
KEYS = ('command', 'install', 'pip', 'env', 'port', 'weights', 'ctx', 'seqs', 'vision', 'wired_limit_reserve_mb')
SHAS = ('1' * 64, '2' * 64, '3' * 64, '4' * 64)
SERVER = 'open("SERVER_STARTED", "w").write("ok")\n'


def before(p):
    """The pin before `runtime` counted (cards and machines count only when not 1)."""
    topology = {k: p[k] for k in ('cards', 'machines') if p.get(k, 1) != 1}
    return hashlib.sha256(json.dumps({**{k: p.get(k) for k in KEYS}, **topology, 'config': lab.config_text(p)}, sort_keys=True).encode()).hexdigest()


def wheel(name, version, sha):
    return {'name': name, 'version': version, 'url': f"{WHEEL_URL}{name.replace('-', '_')}-{version}-py3-none-any.whl", 'sha256': sha}


def runtime(shas=SHAS, root='rt'):
    return {'archive': {'url': ARCHIVE_URL, 'sha256': shas[0], 'into': root}, 'python': f'{root}/python/bin/python3',
            'packages': {'dir': f'{root}/wheels', 'target': f'{root}/site',
                         'wheels': [wheel('fixture-a', '1.0', shas[1]), wheel('fixture-b', '2.0', shas[2]), wheel('huggingface-hub', '1.0', shas[3])]}}


def profile(rt=None, root='rt', launcher='launcher'):
    """A host profile shaped like the Apple MLX ones: a pinned runtime, Hugging Face cache weights and a launcher."""
    return {'id': 'fixture-runtime', 'kind': 'host', 'engine': 'mlx-lm', 'backend': 'metal', 'port': 8080, 'ctx': 32768, 'seqs': 1,
            'vision': False, 'install': f'Task runtime: {root}/ (see runtime)', 'runtime': runtime(root=root) if rt is None else rt,
            'command': [f'{root}/python/bin/python3', '-I', f'{launcher}/serve.py', '--port', '8080'],
            'env': {'PYTHONPATH': f'{root}/site', 'HF_HUB_CACHE': f'{root}/hf-cache', 'HF_HUB_OFFLINE': '1'},
            'weights': [{'repo': 'org/model', 'revision': REV, 'at': f'{root}/hf-cache', 'layout': 'hub', 'files': None, 'ref': 'main'}],
            'config': {'at': f'{launcher}/serve.py', 'text': SERVER}}


def launch(rt=None, **kw):
    """The rendered launch, as the SDKs see it in dist/catalog.json."""
    p = profile(rt, **kw)
    with patch.object(lab, 'profile', return_value=p):
        return lab.render({'model': 'test-model', 'weights': 'org/model@' + REV, 'engine': 'fixture-runtime@host', 'set': {}, 'card': 'apple-m1-8gb'})


def edit(path, value):
    """A launch whose value at `path` (keys and indexes) is replaced; `...` deletes it."""
    def make():
        L = launch()
        *head, last = path
        node = L
        for k in head:
            node = node[k]
        if value is ...:
            del node[last]
        else:
            node[last] = value
        return L
    return make


def without_runtime(change):
    def make():
        L = launch()
        del L['runtime']
        change(L)
        return L
    return make


DIR_WEIGHTS = {'repo': 'org/other', 'revision': 'f' * 40, 'at': 'models/other', 'layout': 'dir', 'files': None}

# Rejected by lab.py check and by both SDKs (sdk/test.sh runs this table through the JS SDK too).
BAD = {
    'archive sha256 too short': edit(['runtime', 'archive', 'sha256'], 'ab' * 31),
    'archive sha256 upper case': edit(['runtime', 'archive', 'sha256'], 'A' * 64),
    'archive sha256 missing': edit(['runtime', 'archive', 'sha256'], ...),
    'archive extra key': edit(['runtime', 'archive', 'strip'], 1),
    'archive over http': edit(['runtime', 'archive', 'url'], ARCHIVE_URL.replace('https', 'http')),
    'archive url with a query': edit(['runtime', 'archive', 'url'], ARCHIVE_URL + '?x=1'),
    'archive url with a fragment': edit(['runtime', 'archive', 'url'], ARCHIVE_URL + '#sha256=' + '1' * 64),
    'archive url with credentials': edit(['runtime', 'archive', 'url'], 'https://user@github.com/a/' + ARCHIVE),
    'archive url with ;': edit(['runtime', 'archive', 'url'], 'https://github.com/a;rm/' + ARCHIVE),
    'archive url with $()': edit(['runtime', 'archive', 'url'], 'https://github.com/$(id)/' + ARCHIVE),
    'archive url with a quote': edit(['runtime', 'archive', 'url'], "https://github.com/a'b/" + ARCHIVE),
    'archive is a zip': edit(['runtime', 'archive', 'url'], ARCHIVE_URL.replace('.tar.gz', '.zip')),
    'archive file decodes to a path': edit(['runtime', 'archive', 'url'], 'https://github.com/a/..%2F..%2F' + ARCHIVE),
    'archive file decodes to a space': edit(['runtime', 'archive', 'url'], 'https://github.com/a/x%20' + ARCHIVE),
    'archive file decodes to a quote': edit(['runtime', 'archive', 'url'], 'https://github.com/a/x%27' + ARCHIVE),
    'into is absolute': edit(['runtime', 'archive', 'into'], '/usr/local'),
    'into is ..': edit(['runtime', 'archive', 'into'], '..'),
    'into climbs': edit(['runtime', 'archive', 'into'], 'rt/../../x'),
    'into has a . part': edit(['runtime', 'archive', 'into'], 'rt/./x'),
    'into has ;': edit(['runtime', 'archive', 'into'], 'rt;id'),
    'into is empty': edit(['runtime', 'archive', 'into'], ''),
    'python is absolute': edit(['runtime', 'python'], '/usr/bin/python3'),
    'python has $()': edit(['runtime', 'python'], '$(id)/python3'),
    'python is a bare name (PATH lookup)': edit(['runtime', 'python'], 'python3'),
    'python outside the archive': edit(['runtime', 'python'], 'other/bin/python3'),
    'python is the archive dir itself': edit(['runtime', 'python'], 'rt'),
    'python without an archive': edit(['runtime', 'archive'], ...),
    'packages without python': edit(['runtime', 'python'], ...),
    'packages extra key': edit(['runtime', 'packages', 'index'], 'https://pypi.org/simple'),
    'packages without target': edit(['runtime', 'packages', 'target'], ...),
    'target is absolute': edit(['runtime', 'packages', 'target'], '/Library/Python'),
    'dir climbs': edit(['runtime', 'packages', 'dir'], 'rt/../..'),
    'dir inside target': edit(['runtime', 'packages', 'dir'], 'rt/site/wheels'),
    'target inside dir': edit(['runtime', 'packages', 'target'], 'rt/wheels/site'),
    'dir is target': edit(['runtime', 'packages', 'dir'], 'rt/site'),
    'no wheels': edit(['runtime', 'packages', 'wheels'], []),
    'wheels not a list': edit(['runtime', 'packages', 'wheels'], {'a': 1}),
    'wheel extra key': edit(['runtime', 'packages', 'wheels', 0, 'extras'], 'cli'),
    'wheel without sha256': edit(['runtime', 'packages', 'wheels', 0, 'sha256'], ...),
    'wheel sha256 short': edit(['runtime', 'packages', 'wheels', 0, 'sha256'], '1' * 63),
    'wheel over http': edit(['runtime', 'packages', 'wheels', 0, 'url'], 'http://files.example.invalid/fixture_a-1.0-py3-none-any.whl'),
    'wheel file is a source archive': edit(['runtime', 'packages', 'wheels', 0, 'url'], WHEEL_URL + 'fixture_a-1.0.tar.gz'),
    'wheel file names another project': edit(['runtime', 'packages', 'wheels', 0, 'name'], 'other'),
    'wheel file names another version': edit(['runtime', 'packages', 'wheels', 0, 'version'], '1.1'),
    'wheel name has a space': edit(['runtime', 'packages', 'wheels', 0, 'name'], 'fixture a'),
    'wheel version has ;': edit(['runtime', 'packages', 'wheels', 0, 'version'], '1.0;id'),
    'wheel listed twice': edit(['runtime', 'packages', 'wheels', 1], wheel('fixture-a', '1.0', '2' * 64)),
    'wheel listed twice under another spelling': edit(['runtime', 'packages', 'wheels', 1], wheel('Fixture_A', '1.0', '2' * 64)),
    'weights without a pinned huggingface_hub': edit(['runtime', 'packages', 'wheels', 2], wheel('fixture-c', '1.0', '4' * 64)),
    'weights with a runtime that has no packages': edit(['runtime', 'packages'], ...),
    'directory weights with a runtime': edit(['weights', 0], DIR_WEIGHTS),
    'hub and directory weights mixed': without_runtime(lambda L: L['weights'].append(DIR_WEIGHTS)),
    'launcher path is absolute': edit(['config', 'at'], '/tmp/serve.py'),
    'launcher path climbs': edit(['config', 'at'], '../serve.py'),
    'runtime extra key': edit(['runtime', 'post'], 'echo hi'),
    'runtime empty': edit(['runtime'], {}),
    'runtime not an object': edit(['runtime'], 'rt/python/bin/python3'),
    'runtime with pip': edit(['pip'], ['mlx-lm==0.31.3']),
    'ref without layout hub': edit(['weights', 0, 'layout'], 'dir'),
    'ref is a path': edit(['weights', 0, 'ref'], 'heads/main'),
    'ref climbs': edit(['weights', 0, 'ref'], '../main'),
    'ref has $()': edit(['weights', 0, 'ref'], '$(id)'),
    'hub revision is a branch': edit(['weights', 0, 'revision'], 'main'),
    'hub cache dir is absolute': edit(['weights', 0, 'at'], '/Users/someone/.cache/huggingface/hub'),
    'hub cache dir has ;': edit(['weights', 0, 'at'], 'rt;id'),
    'hub repo has ;': edit(['weights', 0, 'repo'], 'org/model;id'),
    'hub files climb': edit(['weights', 0, 'files'], ['../config.json']),
}

# Rejected by lab.py check; the SDKs never render these fields for a container, so there is nothing to refuse there.
BAD_LAB = {
    'runtime on a container': lambda: {**launch(), 'kind': None},
    'ref on a container': lambda: {**launch(), 'kind': None, 'runtime': None},
    'hub weights extra key': edit(['weights', 0, 'sha256'], '1' * 64),
}


def lab_rejects(L):
    try:
        lab.check_host_runtime(L)
    except AssertionError:
        return True
    return False


NODE = shutil.which('node')
# bash (what the site's one command runs), sh, and dash (sh on Debian and Ubuntu) when installed
SHELLS = [('bash', '-c'), ('bash', '-ec'), ('sh', '-c')] + ([('dash', '-c')] if shutil.which('dash') else [])


def js(fn, L):
    """`steps` or `command` from the JS SDK for the same launch."""
    out = subprocess.run([NODE, '--input-type=module', '-e',
                          f'import {{readFileSync}} from "node:fs"; const m = await import(process.argv[1]); '
                          f'console.log(JSON.stringify(m.{fn}(JSON.parse(readFileSync(0, "utf8")))));', str(ROOT / 'sdk/js/index.js')],
                         input=json.dumps({'model': 'test-model', 'launch': L}), capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def renderings(L, fn='steps'):
    """Each SDK's output for one launch: Python always, JS when node is installed."""
    out = {'python': getattr(sdk, fn)({'model': 'test-model', 'launch': L})}
    if NODE:
        out['js'] = js(fn, L)
    return out


class ContractTest(unittest.TestCase):
    def test_fixture_passes(self):
        lab.check_host_runtime(launch())
        self.assertEqual(launch()['runtime'], runtime())

    def test_lab_rejects(self):
        for name, make in {**BAD, **BAD_LAB}.items():
            self.assertTrue(lab_rejects(make()), name)

    def test_sdk_refuses_to_render(self):
        for name, make in BAD.items():
            with self.assertRaises(ValueError, msg=name):
                sdk.steps({'model': 'test-model', 'launch': make()})

    def test_launches_without_the_fields_are_untouched(self):
        # no registry profile uses the fields yet; every rendered launch still passes
        for f in sorted(lab.RECIPES.rglob('*.json')):
            L = lab.render(json.loads(f.read_text()))
            self.assertNotIn('runtime', L, f.name)
            lab.check_host_runtime(L)

    def test_check_reports_a_bad_runtime(self):
        bad = profile({**runtime(), 'archive': {**runtime()['archive'], 'sha256': 'x' * 64}})
        r = {'model': 'test-model', 'weights': 'org/model@' + REV, 'engine': 'fixture-runtime@host', 'set': {}, 'card': 'apple-m1-8gb',
             'proof': [{'gates': 'load chat reasoning tools context speed', 'tps': 20.0}]}
        with tempfile.TemporaryDirectory() as d, patch.object(lab, 'ROOT', Path(d)), patch.object(lab, 'RECIPES', Path(d) / 'recipes'), \
                patch.object(lab, 'profile', return_value=bad):
            out = lab.recipe_path(r, lab.render(r)); out.parent.mkdir(parents=True); out.write_text(json.dumps(r))
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(lab.cmd_check(None), 1)
            self.assertIn('runtime.archive.sha256', buf.getvalue())


class PinTest(unittest.TestCase):
    def test_every_existing_pin_unchanged(self):
        # a container pins to its image digest or build commit, which this never touches; every host profile keeps its hash
        ps = [json.loads(f.read_text()) for f in sorted(lab.LAUNCHES.glob('*.json'))]
        hosts = [p for p in ps if p.get('kind') == 'host']
        self.assertTrue(hosts)
        self.assertFalse([p['id'] for p in ps if 'runtime' in p])
        for p in hosts:
            self.assertEqual(lab.pin(p), before(p), p.get('id'))

    def test_runtime_counts(self):
        p = profile()
        without = {k: v for k, v in p.items() if k != 'runtime'}
        self.assertEqual(lab.pin(without), before(without))
        self.assertNotEqual(lab.pin(p), lab.pin(without))
        self.assertNotEqual(lab.pin(p), lab.pin(profile(runtime(('1' * 64, '2' * 64, '5' * 64, '4' * 64)))))  # one wheel hash
        moved = copy.deepcopy(p); moved['weights'][0]['ref'] = 'v1'
        self.assertNotEqual(lab.pin(p), lab.pin(moved))  # the task-local ref


class StepsTest(unittest.TestCase):
    def setUp(self):
        self.steps = sdk.steps({'model': 'test-model', 'launch': launch()})
        self.by = {s['title']: s['code'] for s in self.steps}

    def test_titles(self):
        self.assertEqual([s['title'] for s in self.steps],
                         ['Install the runtime', 'Install the packages', 'Download the weights', 'Write the launcher', 'Start the server'])

    def test_every_setup_step_is_one_strict_subshell(self):
        for s in self.steps[:-1]:
            self.assertTrue(s['code'].startswith('(\nset -e\n') and s['code'].endswith('\n)'), s['title'])

    def test_command_stops_at_the_first_failure(self):
        for name, c in renderings(launch(), 'command').items():
            self.assertTrue(c.startswith('set -e\n\n'), name)

    def test_no_prose_no_flat_weights_no_ambient_tools(self):
        code = '\n'.join(s['code'] for s in self.steps)
        self.assertNotIn('Task runtime', code)  # the free-text install is not rendered when runtime is pinned
        self.assertNotIn('--local-dir', code)
        self.assertNotIn('hf download', code)
        self.assertIn("./rt/python/bin/python3 -I -c 'import sys; sys.path.insert(0, sys.argv[1]); from huggingface_hub import snapshot_download",
                      self.by['Download the weights'])
        self.assertIn('./rt/python/bin/python3 -I -m pip --isolated install', self.by['Install the packages'])

    def test_archive_is_checked_before_extracting(self):
        code = self.by['Install the runtime']
        self.assertIn(f'curl -fL -o "$s"/{ARCHIVE} {ARCHIVE_URL}', code)
        self.assertLess(code.index('shasum -a 256 -c'), code.index('tar -xzf'))

    def test_packages_are_hash_locked_offline_and_staged(self):
        code = self.by['Install the packages']
        for flag in ('--no-index', '--only-binary :all:', '--require-hashes', '--target "$s/t"'):
            self.assertIn(flag, code)
        self.assertLess(code.index('shasum -a 256 -c SHA256SUMS'), code.index('pip --isolated install'))
        self.assertLess(code.index('pip --isolated install'), code.index('mv "$s/t" rt/site'))

    def test_existing_shapes_unchanged(self):
        # without the new fields: the old install, pip, --local-dir and launcher rendering, and no `set -e` in command()
        L = launch()
        del L['runtime']
        L['pip'] = ['mlx-lm==0.31.3']
        L['weights'] = [{'repo': 'org/model', 'revision': REV, 'at': 'models/model', 'layout': 'dir', 'files': None}]
        st = {s['title']: s['code'] for s in sdk.steps({'model': 'test-model', 'launch': L})}
        self.assertEqual(st['Install the engine'], 'python3 -m pip install mlx-lm==0.31.3')
        self.assertEqual(st['Download the weights'], f'hf download org/model \\\n  --revision {REV} \\\n  --local-dir models/model')
        self.assertEqual(st['Write the launcher'], f"mkdir -p launcher\ncat > launcher/serve.py <<'EOF'\n{SERVER.rstrip()}\nEOF")
        self.assertFalse(sdk.command({'model': 'test-model', 'launch': L}).startswith('set -e'))


def make_wheel(d, name, version, requires=(), module='VERSION = 1\n'):
    """A minimal pure-Python wheel that pip will install."""
    dist = name.replace('-', '_')
    f = Path(d) / f'{dist}-{version}-py3-none-any.whl'
    info = f'{dist}-{version}.dist-info'
    files = {f'{dist}/__init__.py': module,
             f'{info}/METADATA': f'Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n' + ''.join(f'Requires-Dist: {r}\n' for r in requires),
             f'{info}/WHEEL': 'Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n'}
    digest = lambda v: base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b'=').decode()
    record = ''.join(f'{k},sha256={digest(v)},{len(v.encode())}\n' for k, v in files.items()) + f'{info}/RECORD,,\n'
    with zipfile.ZipFile(f, 'w') as z:
        for k, v in {**files, f'{info}/RECORD': record}.items():
            z.writestr(k, v)
    return f


# The pinned huggingface_hub stand-in: makes the snapshot (unless FAKE_HF_MISS) and records which copy ran.
HUB = '''import os
def snapshot_download(repo_id, revision=None, cache_dir=None, allow_patterns=None):
    open("HUB_USED", "w").write(os.path.abspath(__file__))
    if os.environ.get("FAKE_HF_MISS"):
        return cache_dir
    snap = os.path.join(cache_dir, "models--" + repo_id.replace("/", "--"), "snapshots", revision)
    os.makedirs(snap, exist_ok=True)
    open(os.path.join(snap, "config.json"), "w").write("{}")
    return snap
'''
FAKE_CURL = '''#!/bin/sh
# curl -fL -o FILE URL: copy the URL's file from $FAKE_SERVER, or fail like curl -f on a 404
out=; url=
while [ $# -gt 0 ]; do case "$1" in -o) out=$2; shift 2;; -*) shift;; *) url=$1; shift;; esac; done
name=$(printf '%s' "${url##*/}" | sed 's/%2B/+/g')
[ -f "$FAKE_SERVER/$name" ] || exit 22
cp "$FAKE_SERVER/$name" "$out"
'''
# Anything looked up on PATH or planted in the working directory instead of the pinned runtime: record it and fail.
TRAP = '#!/bin/sh\necho "$0 $*" >> "$TRAPPED"\nexit 73\n'


def tree(p):
    """Every file and link under p with its content hash or target, so two trees compare exactly."""
    p = Path(p)
    if not p.exists() and not p.is_symlink():
        return None
    return {str(f.relative_to(p)): ('link:' + os.readlink(f)) if f.is_symlink() else
            hashlib.sha256(f.read_bytes()).hexdigest() if f.is_file() else 'dir' for f in sorted(p.rglob('*'))}


def staging_left(p):
    return [str(f) for f in Path(p).rglob('*') if '.local-ai-' in f.name]


@unittest.skipUnless(shutil.which('shasum') and shutil.which('tar') and shutil.which('bash') and shutil.which('mktemp')
                     and subprocess.run([sys.executable, '-I', '-m', 'pip', '--version'], capture_output=True).returncode == 0,
                     'needs shasum, tar, bash, mktemp and pip')
class SandboxTest(unittest.TestCase):
    """Run the rendered steps for real in a temp directory: real shasum, tar, mktemp and pip; curl reads local files."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.d = Path(self.tmp.name)
        self.server, self.bin, self.outside = d / 'server', d / 'bin', d / 'outside'
        for p in (self.server, self.bin, self.outside):
            p.mkdir()
        (self.outside / 'victim').write_text('untouched\n')
        (self.bin / 'curl').write_text(FAKE_CURL)
        for name in ('hf', 'python3', 'python', 'pip', 'pip3', 'huggingface-cli'):
            (self.bin / name).write_text(TRAP)
        for f in self.bin.iterdir():
            f.chmod(0o755)
        # the "interpreter archive": python/bin/python3 hands off to this test's own interpreter by absolute path
        src = d / 'src' / 'python' / 'bin'; src.mkdir(parents=True)
        (src / 'python3').write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n'); (src / 'python3').chmod(0o755)
        subprocess.run(['tar', '-czf', str(self.server / ARCHIVE), '-C', str(d / 'src'), 'python'], check=True)
        a = make_wheel(self.server, 'fixture-a', '1.0', requires=['fixture-b'])
        b = make_wheel(self.server, 'fixture-b', '2.0')
        h = make_wheel(self.server, 'huggingface-hub', '1.0', module=HUB)
        self.shas = tuple(hashlib.sha256(f.read_bytes()).hexdigest() for f in (self.server / ARCHIVE, a, b, h))
        self.n = 0

    def tearDown(self):
        self.tmp.cleanup()

    def work(self):
        """A fresh working directory, with a planted python3 that must never run."""
        self.n += 1
        w = self.d / f'work{self.n}'
        w.mkdir()
        (w / 'python3').write_text(TRAP); (w / 'python3').chmod(0o755)
        return w

    def sh(self, code, work, shell=('sh', '-c'), **env):
        return subprocess.run([*shell, code], cwd=work, capture_output=True, text=True,
                              env={'PATH': f'{self.bin}:/usr/bin:/bin', 'FAKE_SERVER': str(self.server), 'HOME': str(work),
                                   'TRAPPED': str(work / 'TRAPPED'), **env})

    def step(self, L, title, work, sdk_name='python', **env):
        code = next(s['code'] for s in renderings(L)[sdk_name] if s['title'] == title)
        return self.sh(code, work, **env)

    def L(self, shas=None, root='rt'):
        return launch(runtime(shas or self.shas, root=root), root=root)

    def assertNothingRan(self, work):
        self.assertFalse((work / 'TRAPPED').exists(), (work / 'TRAPPED').read_text() if (work / 'TRAPPED').exists() else '')

    # 1. the whole command: success, and each failure stops every later stage with a non-zero exit, in either SDK

    def test_whole_script_runs_on_the_pinned_runtime(self):
        for name, script in renderings(self.L(), 'command').items():
            for shell in SHELLS:
                w = self.work()
                r = self.sh(script, w, shell)
                self.assertEqual(r.returncode, 0, (name, shell, r.stderr[-2000:]))
                self.assertEqual((w / 'SERVER_STARTED').read_text(), 'ok')
                self.assertEqual((w / 'rt/hf-cache/models--org--model/refs/main').read_text(), REV)
                # 5. the weights came from the runtime's own huggingface_hub, not an hf on PATH
                self.assertEqual(Path((w / 'HUB_USED').read_text()).resolve(), (w / 'rt/site/huggingface_hub/__init__.py').resolve())
                # 4. nothing on PATH or planted in the working directory ran
                self.assertNothingRan(w)
                self.assertEqual(staging_left(w), [])

    def test_whole_script_stops_at_each_failure(self):
        bad = lambda i: tuple('0' * 64 if j == i else s for j, s in enumerate(self.shas))
        cases = {  # name: (launch, env, set up, what must not exist afterwards)
            'archive hash': (self.L(bad(0)), {}, None, ['rt/python', 'rt/site', 'rt/hf-cache', 'launcher', 'SERVER_STARTED']),
            'wheel hash': (self.L(bad(2)), {}, None, ['rt/site', 'rt/wheels', 'rt/hf-cache', 'launcher', 'SERVER_STARTED']),
            'missing snapshot': (self.L(), {'FAKE_HF_MISS': '1'}, None, ['rt/hf-cache/models--org--model/refs', 'launcher', 'SERVER_STARTED']),
            'launcher': (self.L(), {}, lambda w: (w / 'launcher').write_text('a file in the way\n'), ['SERVER_STARTED']),
        }
        for case, (L, env, setup, absent) in cases.items():
            for name, script in renderings(L, 'command').items():
                for shell in SHELLS:
                    w = self.work()
                    if setup:
                        setup(w)
                    r = self.sh(script, w, shell, **env)
                    self.assertNotEqual(r.returncode, 0, (case, name, shell))
                    for a in absent:
                        self.assertFalse((w / a).exists(), (case, name, shell, a))
                    self.assertFalse((w / 'SERVER_STARTED').exists(), (case, name, shell))
                    self.assertEqual(staging_left(w), [], (case, name, shell))

    # 2. packages: a fresh staged target, published only after the whole install succeeded

    def install(self, w, L=None):
        L = L or self.L()
        for title in ('Install the runtime', 'Install the packages'):
            r = self.step(L, title, w)
            self.assertEqual(r.returncode, 0, (title, r.stderr[-2000:]))

    def test_reinstall_replaces_a_dirty_target_exactly(self):
        ref = self.work(); self.install(ref)
        w = self.work(); self.install(w)
        (w / 'rt/site/fixture_a/__init__.py').write_text('TAMPERED = True\n')
        (w / 'rt/site/stale_pkg').mkdir(); (w / 'rt/site/stale_pkg/__init__.py').write_text('')
        (w / 'rt/wheels/stale-9-py3-none-any.whl').write_text('')
        r = self.step(self.L(), 'Install the packages', w)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertEqual(tree(w / 'rt/site'), tree(ref / 'rt/site'))
        self.assertEqual(tree(w / 'rt/wheels'), tree(ref / 'rt/wheels'))
        self.assertEqual(staging_left(w), [])

    def test_failed_reinstall_keeps_the_prior_target(self):
        w = self.work(); self.install(w)
        site, wheels = tree(w / 'rt/site'), tree(w / 'rt/wheels')
        rt = runtime(self.shas); rt['packages']['wheels'] = [rt['packages']['wheels'][0], rt['packages']['wheels'][2]]  # fixture-b missing
        for case, L in {'wrong wheel hash': self.L(self.shas[:2] + ('e' * 64,) + self.shas[3:]), 'incomplete lock': launch(rt)}.items():
            r = self.step(L, 'Install the packages', w)
            self.assertNotEqual(r.returncode, 0, case)
            self.assertEqual(tree(w / 'rt/site'), site, case)
            self.assertEqual(tree(w / 'rt/wheels'), wheels, case)
            self.assertEqual(staging_left(w), [], case)

    def test_reinstalled_runtime_replaces_only_the_archive_entries(self):
        w = self.work(); self.install(w)
        self.assertEqual(self.step(self.L(), 'Download the weights', w).returncode, 0)
        (w / 'rt/python/STALE').write_text('')
        site, cache = tree(w / 'rt/site'), tree(w / 'rt/hf-cache')
        r = self.step(self.L(), 'Install the runtime', w)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((w / 'rt/python/STALE').exists())
        self.assertEqual((tree(w / 'rt/site'), tree(w / 'rt/hf-cache')), (site, cache))

    def test_wrong_archive_hash_leaves_the_prior_runtime(self):
        w = self.work(); self.install(w)
        before_ = tree(w / 'rt')
        r = self.step(self.L(('f' * 64,) + self.shas[1:]), 'Install the runtime', w)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(tree(w / 'rt'), before_)

    def test_missing_wheel_stops(self):
        w = self.work(); self.step(self.L(), 'Install the runtime', w)
        (self.server / 'fixture_b-2.0-py3-none-any.whl').unlink()
        r = self.step(self.L(), 'Install the packages', w)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((w / 'rt/site').exists() or (w / 'rt/wheels').exists())

    # 3. no write through a symlink, at the destination or any directory above it, in either SDK's rendering

    def test_symlinks_are_refused(self):
        def link(rel, to=None):
            def make(w):
                (w / rel).parent.mkdir(parents=True, exist_ok=True)
                if (w / rel).is_dir() and not (w / rel).is_symlink():
                    shutil.rmtree(w / rel)
                elif (w / rel).exists():
                    (w / rel).unlink()
                (w / rel).symlink_to(to or self.outside)
            return make
        m = 'rt/hf-cache/models--org--model'
        cases = {  # name: (launch, prerequisite steps, symlink, step)
            'into leaf': (self.L(), [], link('rt'), 'Install the runtime'),
            'archive entry': (self.L(), [], link('rt/python'), 'Install the runtime'),
            'into parent': (self.L(root='opt/rt'), [], link('opt'), 'Install the runtime'),
            'wheel dir leaf': (self.L(), ['Install the runtime'], link('rt/wheels'), 'Install the packages'),
            'target leaf': (self.L(), ['Install the runtime'], link('rt/site'), 'Install the packages'),
            'packages parent': (self.L(root='opt/rt'), [], link('opt'), 'Install the packages'),
            'cache leaf': (self.L(), ['Install the runtime', 'Install the packages'], link('rt/hf-cache'), 'Download the weights'),
            'model dir': (self.L(), ['Install the runtime', 'Install the packages'], link(m), 'Download the weights'),
            'refs dir': (self.L(), ['Install the runtime', 'Install the packages'], link(m + '/refs'), 'Download the weights'),
            'ref leaf': (self.L(), ['Install the runtime', 'Install the packages'], link(m + '/refs/main', self.outside / 'victim'), 'Download the weights'),
            'cache parent': (self.L(root='opt/rt'), [], link('opt'), 'Download the weights'),
            'launcher leaf': (self.L(), [], link('launcher/serve.py', self.outside / 'victim'), 'Write the launcher'),
            'launcher parent': (self.L(), [], link('launcher'), 'Write the launcher'),
        }
        for case, (L, pre, make, title) in cases.items():
            for name in renderings(L):
                w = self.work()
                for p in pre:
                    self.assertEqual(self.step(L, p, w, name).returncode, 0, (case, p))
                make(w)
                was = tree(self.outside)
                r = self.step(L, title, w, name)
                self.assertNotEqual(r.returncode, 0, (case, name))
                self.assertIn('is a symlink', r.stderr, (case, name))
                self.assertEqual(tree(self.outside), was, (case, name))

    # 5. the weights step needs nothing on PATH

    def test_weights_without_any_hf_on_path(self):
        w = self.work(); self.install(w)
        r = self.step(self.L(), 'Download the weights', w, PATH='/usr/bin:/bin')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((w / 'rt/hf-cache/models--org--model/refs/main').read_text(), REV)

    def test_missing_snapshot_writes_no_ref(self):
        w = self.work(); self.install(w)
        r = self.step(self.L(), 'Download the weights', w, FAKE_HF_MISS='1')
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((w / 'rt/hf-cache/models--org--model/refs').exists())


if __name__ == '__main__':
    unittest.main()
