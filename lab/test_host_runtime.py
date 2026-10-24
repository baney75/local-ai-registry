"""The optional pinned host runtime (docs/design.md, "A pinned host runtime"): validation, pins, rendered steps, and the
steps themselves run in a sandbox with real shasum, tar and pip and stand-ins for curl and hf (no network)."""
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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'sdk' / 'python'))
import local_ai_registry as sdk  # noqa: E402

REV = '0123456789abcdef0123456789abcdef01234567'
ARCHIVE = 'cpython-3.12.15+20261021-aarch64-apple-darwin-install_only.tar.gz'
ARCHIVE_URL = 'https://github.com/astral-sh/python-build-standalone/releases/download/20261021/' + ARCHIVE.replace('+', '%2B')
WHEEL_URL = 'https://files.example.invalid/packages/'
KEYS = ('command', 'install', 'pip', 'env', 'port', 'weights', 'ctx', 'seqs', 'vision', 'wired_limit_reserve_mb')


def before(p):
    """The pin before `runtime` counted (cards and machines count only when not 1)."""
    topology = {k: p[k] for k in ('cards', 'machines') if p.get(k, 1) != 1}
    return hashlib.sha256(json.dumps({**{k: p.get(k) for k in KEYS}, **topology, 'config': lab.config_text(p)}, sort_keys=True).encode()).hexdigest()


def wheel(name, version, sha):
    return {'name': name, 'version': version, 'url': f"{WHEEL_URL}{name.replace('-', '_')}-{version}-py3-none-any.whl", 'sha256': sha}


def runtime(shas=('1' * 64, '2' * 64, '3' * 64)):
    return {'archive': {'url': ARCHIVE_URL, 'sha256': shas[0], 'into': 'rt'}, 'python': 'rt/python/bin/python3',
            'packages': {'dir': 'rt/wheels', 'target': 'rt/site',
                         'wheels': [wheel('fixture-a', '1.0', shas[1]), wheel('fixture-b', '2.0', shas[2])]}}


def profile(rt=None):
    """A host profile shaped like the Apple MLX ones, with a pinned runtime and Hugging Face cache weights."""
    return {'id': 'fixture-runtime', 'kind': 'host', 'engine': 'mlx-lm', 'backend': 'metal', 'port': 8080, 'ctx': 32768, 'seqs': 1,
            'vision': False, 'install': 'Task runtime: rt/ (see runtime)', 'runtime': runtime() if rt is None else rt,
            'command': ['rt/python/bin/python3', '-I', '-m', 'mlx_lm', 'server', '--model', 'org/model', '--host', '127.0.0.1', '--port', '8080'],
            'env': {'PYTHONPATH': 'rt/site', 'HF_HUB_CACHE': 'rt/hf-cache', 'HF_HUB_OFFLINE': '1'},
            'weights': [{'repo': 'org/model', 'revision': REV, 'at': 'rt/hf-cache', 'layout': 'hub', 'files': None, 'ref': 'main'}]}


def launch(rt=None):
    """The rendered launch, as the SDKs see it in dist/catalog.json."""
    p = profile(rt)
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
    'packages without python': edit(['runtime', 'python'], ...),
    'packages extra key': edit(['runtime', 'packages', 'index'], 'https://pypi.org/simple'),
    'packages without target': edit(['runtime', 'packages', 'target'], ...),
    'target is absolute': edit(['runtime', 'packages', 'target'], '/Library/Python'),
    'dir climbs': edit(['runtime', 'packages', 'dir'], 'rt/../..'),
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
        # no registry profile uses the fields yet; every rendered launch still passes, and its steps never mention them
        for f in sorted(lab.RECIPES.rglob('*.json')):
            r = json.loads(f.read_text())
            L = lab.render(r)
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
        other = profile(runtime(('1' * 64, '2' * 64, '4' * 64)))
        self.assertNotEqual(lab.pin(p), lab.pin(other))  # one wheel hash
        moved = copy.deepcopy(p); moved['weights'][0]['ref'] = 'v1'
        self.assertNotEqual(lab.pin(p), lab.pin(moved))  # the task-local ref


class StepsTest(unittest.TestCase):
    def setUp(self):
        self.steps = sdk.steps({'model': 'test-model', 'launch': launch()})
        self.by = {s['title']: s['code'] for s in self.steps}

    def test_titles(self):
        self.assertEqual([s['title'] for s in self.steps],
                         ['Install the runtime', 'Install the packages', 'Download the weights', 'Start the server'])

    def test_no_prose_and_no_flat_weights(self):
        code = '\n'.join(s['code'] for s in self.steps)
        self.assertNotIn('Task runtime', code)  # the free-text install is not rendered when runtime is pinned
        self.assertFalse([line for line in code.splitlines() if line.lstrip().startswith('#')])
        self.assertNotIn('--local-dir', self.by['Download the weights'])
        self.assertIn('--cache-dir rt/hf-cache', self.by['Download the weights'])

    def test_archive_is_checked_before_extracting(self):
        code = self.by['Install the runtime']
        self.assertIn(f'curl -fL -o {ARCHIVE} {ARCHIVE_URL}', code)
        self.assertLess(code.index('shasum -a 256 -c'), code.index('tar -xzf'))
        self.assertIn(' && \\\n', code)

    def test_packages_are_hash_locked_and_offline(self):
        code = self.by['Install the packages']
        for flag in ('-I -m pip install', '--no-index', '--only-binary :all:', '--require-hashes', '--target rt/site'):
            self.assertIn(flag, code)
        self.assertLess(code.index('shasum -a 256 -c SHA256SUMS'), code.index('pip install'))

    def test_existing_shapes_unchanged(self):
        # without the new fields: the old install, pip and --local-dir rendering
        L = launch()
        del L['runtime']
        L['pip'] = ['mlx-lm==0.31.3']
        L['weights'] = [{'repo': 'org/model', 'revision': REV, 'at': 'models/model', 'layout': 'dir', 'files': None}]
        st = {s['title']: s['code'] for s in sdk.steps({'model': 'test-model', 'launch': L})}
        self.assertEqual(st['Install the engine'], 'python3 -m pip install mlx-lm==0.31.3')
        self.assertEqual(st['Download the weights'], f'hf download org/model \\\n  --revision {REV} \\\n  --local-dir models/model')


def make_wheel(d, name, version, requires=()):
    """A minimal pure-Python wheel that pip will install."""
    dist = name.replace('-', '_')
    f = Path(d) / f'{dist}-{version}-py3-none-any.whl'
    info = f'{dist}-{version}.dist-info'
    files = {f'{dist}/__init__.py': f'VERSION = {version!r}\n',
             f'{info}/METADATA': f'Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n' + ''.join(f'Requires-Dist: {r}\n' for r in requires),
             f'{info}/WHEEL': 'Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n'}
    record = ''.join(f"{k},sha256={__import__('base64').urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b'=').decode()},{len(v.encode())}\n"
                     for k, v in files.items()) + f'{info}/RECORD,,\n'
    with zipfile.ZipFile(f, 'w') as z:
        for k, v in {**files, f'{info}/RECORD': record}.items():
            z.writestr(k, v)
    return f


FAKE_CURL = '''#!/bin/sh
# curl -fL -o FILE URL: copy the URL's file from $FAKE_SERVER, or fail like curl -f on a 404
out=; url=
while [ $# -gt 0 ]; do case "$1" in -o) out=$2; shift 2;; -*) shift;; *) url=$1; shift;; esac; done
name=$(printf '%s' "${url##*/}" | sed 's/%2B/+/g')
[ -f "$FAKE_SERVER/$name" ] || exit 22
cp "$FAKE_SERVER/$name" "$out"
'''
FAKE_HF = '''#!/bin/sh
# hf download REPO --revision REV --cache-dir DIR: make the snapshot unless $FAKE_HF_MISS (a download that left none)
repo=$2; rev=; dir=
while [ $# -gt 0 ]; do case "$1" in --revision) rev=$2; shift 2;; --cache-dir) dir=$2; shift 2;; *) shift;; esac; done
[ -n "$FAKE_HF_MISS" ] && exit 0
snap="$dir/models--$(printf '%s' "$repo" | sed 's#/#--#')/snapshots/$rev"
mkdir -p "$snap" && echo '{}' > "$snap/config.json"
'''


@unittest.skipUnless(shutil.which('shasum') and shutil.which('tar') and shutil.which('sh'), 'needs shasum, tar and sh')
class SandboxTest(unittest.TestCase):
    """Run the rendered steps for real in a temp directory: real shasum, tar and pip; curl and hf read local files."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.work, self.server, self.bin = d / 'work', d / 'server', d / 'bin'
        for p in (self.work, self.server, self.bin):
            p.mkdir()
        for name, text in (('curl', FAKE_CURL), ('hf', FAKE_HF)):
            (self.bin / name).write_text(text); (self.bin / name).chmod(0o755)
        # the "interpreter archive": python/bin/python3 hands off to this test's own interpreter
        src = d / 'src' / 'python' / 'bin'; src.mkdir(parents=True)
        (src / 'python3').write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n'); (src / 'python3').chmod(0o755)
        subprocess.run(['tar', '-czf', str(self.server / ARCHIVE), '-C', str(d / 'src'), 'python'], check=True)
        a = make_wheel(self.server, 'fixture-a', '1.0', requires=['fixture-b'])
        b = make_wheel(self.server, 'fixture-b', '2.0')
        self.shas = tuple(hashlib.sha256(f.read_bytes()).hexdigest() for f in (self.server / ARCHIVE, a, b))

    def tearDown(self):
        self.tmp.cleanup()

    def run_step(self, L, title, **env):
        code = next(s['code'] for s in sdk.steps({'model': 'test-model', 'launch': L}) if s['title'] == title)
        return subprocess.run(['sh', '-c', code], cwd=self.work, capture_output=True, text=True,
                              env={'PATH': f"{self.bin}:{os.environ['PATH']}", 'FAKE_SERVER': str(self.server), 'HOME': str(self.work), **env})

    def test_runtime_installs(self):
        L = launch(runtime(self.shas))
        r = self.run_step(L, 'Install the runtime')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.work / 'rt/python/bin/python3').exists())

    def test_wrong_archive_hash_stops_before_extracting(self):
        L = launch(runtime(('f' * 64, *self.shas[1:])))
        r = self.run_step(L, 'Install the runtime')
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((self.work / 'rt').exists())

    @unittest.skipUnless(subprocess.run([sys.executable, '-I', '-m', 'pip', '--version'], capture_output=True).returncode == 0, 'needs pip')
    def test_packages_install_offline(self):
        L = launch(runtime(self.shas))
        self.assertEqual(self.run_step(L, 'Install the runtime').returncode, 0)
        r = self.run_step(L, 'Install the packages', PIP_CONFIG_FILE=os.devnull)
        self.assertEqual(r.returncode, 0, r.stderr)
        for m in ('fixture_a', 'fixture_b'):
            self.assertTrue((self.work / 'rt/site' / m / '__init__.py').exists(), m)
        self.assertEqual((self.work / 'rt/wheels/requirements.lock.txt').read_text().splitlines(),
                         [f'fixture-a==1.0 --hash=sha256:{self.shas[1]}', f'fixture-b==2.0 --hash=sha256:{self.shas[2]}'])

    @unittest.skipUnless(subprocess.run([sys.executable, '-I', '-m', 'pip', '--version'], capture_output=True).returncode == 0, 'needs pip')
    def test_incomplete_bundle_fails(self):
        # fixture-a needs fixture-b; a lock without it cannot install offline
        rt = runtime(self.shas)
        rt['packages']['wheels'] = rt['packages']['wheels'][:1]
        L = launch(rt)
        self.assertEqual(self.run_step(L, 'Install the runtime').returncode, 0)
        r = self.run_step(L, 'Install the packages', PIP_CONFIG_FILE=os.devnull)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((self.work / 'rt/site/fixture_a').exists())

    def test_wrong_wheel_hash_stops_before_pip(self):
        L = launch(runtime((self.shas[0], self.shas[1], 'e' * 64)))
        r = self.run_step(L, 'Install the packages')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('FAILED', r.stdout + r.stderr)
        self.assertFalse((self.work / 'rt/wheels/requirements.lock.txt').exists())
        self.assertFalse((self.work / 'rt/site').exists())

    def test_missing_wheel_stops(self):
        (self.server / 'fixture_b-2.0-py3-none-any.whl').unlink()
        r = self.run_step(launch(runtime(self.shas)), 'Install the packages')
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((self.work / 'rt/wheels/SHA256SUMS').exists())

    def test_hub_cache_gets_its_ref(self):
        r = self.run_step(launch(), 'Download the weights')
        self.assertEqual(r.returncode, 0, r.stderr)
        ref = self.work / 'rt/hf-cache/models--org--model/refs/main'
        self.assertEqual(ref.read_text(), REV)
        self.assertTrue((self.work / f'rt/hf-cache/models--org--model/snapshots/{REV}/config.json').exists())

    def test_missing_snapshot_writes_no_ref(self):
        r = self.run_step(launch(), 'Download the weights', FAKE_HF_MISS='1')
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((self.work / 'rt/hf-cache/models--org--model/refs').exists())


if __name__ == '__main__':
    unittest.main()
