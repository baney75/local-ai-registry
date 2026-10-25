#!/usr/bin/env python3
"""Run a task-owned server under a supervisor that can always take down the whole tree it started, and nothing else.

  groupwatch.py run  --state DIR [--max-seconds N] [--min-free-pct P] [--max-swap-mb M] [--trigger-file F] -- CMD ...
  groupwatch.py stop --state DIR

`run` starts CMD in a new session (own process group, pgid = its pid). Every second it records CMD and its
descendants, found by walking parent links from CMD, as (pid, start time) identities. A descendant that leaves the
group (setsid) stays recorded. Ownership is never inferred from argv or from group membership alone.
Teardown happens on: lifetime cap, macOS memory guard (memory_pressure free % below P, or swap used above M MB),
the trigger file, or SIGTERM/SIGINT/SIGHUP to the supervisor (`stop`).
Teardown, per round (SIGTERM, wait up to 15 s, then SIGKILL, wait up to 5 s):
  - signal the whole group with killpg only if every process currently in that group is a recorded identity;
    otherwise skip killpg and log the unknown members;
  - signal each recorded identity that is still alive, rechecking its start time right before the signal.
Then verify that no recorded identity survives. Zombies count as dead; the supervisor reaps its own child.
`stop` signals the supervisor only after its pid, start time and exact argv (as ps showed them when `run` began) match.
Log: DIR/groupwatch.log. Exit: CMD's exit code, or 3 when the guard, trigger, cap or a stop tore it down.
"""
import os, re, signal, subprocess, sys, time

def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")

def ps_table():
    """pid -> (ppid, pgid, lstart, args). Zombies (stat Z) are dead for our purposes and left out."""
    out = subprocess.run(["ps", "-axo", "pid=,ppid=,pgid=,stat=,lstart=,args="], capture_output=True, text=True, timeout=10).stdout
    t = {}
    for line in out.splitlines():
        p = line.split(None, 4)
        if len(p) < 5 or p[3].startswith("Z"):
            continue
        rest = p[4]  # lstart is 5 words ("Fri Oct 23 16:41:51 2026"), then args
        w = rest.split(None, 5)
        t[int(p[0])] = (int(p[1]), int(p[2]), " ".join(w[:5]), w[5] if len(w) > 5 else "")
    return t

def descendants(root, table):
    kids, seen, stack = {}, set(), [root]
    for pid, v in table.items():
        kids.setdefault(v[0], []).append(pid)
    while stack:
        p = stack.pop()
        if p in seen or p not in table:
            continue
        seen.add(p)
        stack += kids.get(p, [])
    return seen

def mac_memory():
    free = swap = None
    try:
        m = re.search(r"free percentage: (\d+)%", subprocess.run(["memory_pressure", "-Q"], capture_output=True, text=True, timeout=10).stdout)
        free = int(m.group(1)) if m else None
    except Exception:
        pass
    try:
        m = re.search(r"used = ([\d.]+)M", subprocess.run(["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True, timeout=5).stdout)
        swap = float(m.group(1)) if m else None
    except Exception:
        pass
    return free, swap

class Supervisor:
    def __init__(self, state):
        self.state, self.known, self.proc = state, {}, None  # known: pid -> lstart
        self.log = open(os.path.join(state, "groupwatch.log"), "a", buffering=1)

    def say(self, msg):
        self.log.write(f"{now()} {msg}\n")

    def record(self, root):
        table = ps_table()
        for pid in descendants(root, table):
            self.known.setdefault(pid, table[pid][2])
        return table

    def owned_alive(self, table=None):
        if self.proc is not None:
            self.proc.poll()  # reap our own direct child so it never shows as a live zombie
        table = table or ps_table()
        return {pid for pid, st in self.known.items() if pid in table and table[pid][2] == st}

    def signal_owned(self, pid, sig):
        t = ps_table()
        if pid in t and t[pid][2] == self.known.get(pid):  # identity rechecked right before the signal
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass

    def teardown(self, root, pgid, reason):
        self.record(root)
        self.say(f"TEARDOWN ({reason}): pgid {pgid}, recorded {sorted(self.known)}")
        for sig, wait in ((signal.SIGTERM, 15), (signal.SIGKILL, 5)):
            table = ps_table()
            live = self.owned_alive(table)
            if not live:
                break
            members = {pid for pid, v in table.items() if v[1] == pgid}
            foreign = {pid for pid in members if self.known.get(pid) != table[pid][2]}
            if members and not foreign:
                self.say(f"send {sig.name} to group {pgid} (members {sorted(members)}, all recorded)")
                try:
                    os.killpg(pgid, sig)
                except ProcessLookupError:
                    pass
            elif foreign:
                self.say(f"skip killpg {pgid}: unrecorded members {sorted(foreign)}")
            self.say(f"send {sig.name} to recorded pids {sorted(live)}")
            for pid in live:
                self.signal_owned(pid, sig)
            t = time.monotonic()
            while time.monotonic() - t < wait and self.owned_alive():
                time.sleep(0.2)
        left = self.owned_alive()
        self.say(f"after teardown alive: {sorted(left) if left else 'none'}")
        return not left

def run(argv):
    a = {"--max-seconds": None, "--min-free-pct": None, "--max-swap-mb": None, "--trigger-file": None, "--state": None}
    if "--" not in argv:
        sys.exit("groupwatch run: put -- before the command")
    i = 0
    while argv[i] != "--":
        if argv[i] not in a or i + 1 >= len(argv) or argv[i + 1] == "--":
            sys.exit(f"groupwatch run: unknown option or missing value: {argv[i]}")
        a[argv[i]] = argv[i + 1]
        i += 2
    cmd, state = argv[i + 1:], a["--state"]
    if not state or not cmd:
        sys.exit("groupwatch run: --state DIR and a command are required")
    os.makedirs(state, exist_ok=True)
    sv = Supervisor(state)
    me = ps_table()[os.getpid()]
    open(os.path.join(state, "supervisor.id"), "w").write(f"{os.getpid()}\n{me[2]}\n{me[3]}\n")
    proc = subprocess.Popen(cmd, start_new_session=True)
    sv.proc = proc
    pgid = os.getpgid(proc.pid)
    open(os.path.join(state, "child.pid"), "w").write(f"{proc.pid}\n")
    sv.say(f"options: " + " ".join(f"{k}={v}" for k, v in a.items() if k != "--state" and v is not None))
    sv.say(f"supervisor pid {os.getpid()} started child pid {proc.pid} pgid {pgid}: {' '.join(cmd)}")
    stop = {"why": ""}
    for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(s, lambda n, f: stop.update(why=f"supervisor got {signal.Signals(n).name}"))
    t0, last_mem = time.monotonic(), 0.0
    while proc.poll() is None and not stop["why"]:
        sv.record(proc.pid)
        if a["--max-seconds"] and time.monotonic() - t0 > float(a["--max-seconds"]):
            stop["why"] = f"lifetime cap {a['--max-seconds']} s"
        if a["--trigger-file"] and os.path.exists(str(a["--trigger-file"])):
            stop["why"] = "trigger file"
        if (a["--min-free-pct"] or a["--max-swap-mb"]) and time.monotonic() - last_mem >= 5:
            last_mem = time.monotonic()
            free, swap = mac_memory()
            sv.say(f"mem free_pct={free} swap_used_mb={swap}")
            if a["--min-free-pct"] and free is not None and free < int(a["--min-free-pct"]):
                stop["why"] = f"memory guard: free {free}% < {a['--min-free-pct']}%"
            if a["--max-swap-mb"] and swap is not None and swap > float(a["--max-swap-mb"]):
                stop["why"] = f"memory guard: swap {swap} MB > {a['--max-swap-mb']} MB"
        time.sleep(1)
    if stop["why"]:
        ok = sv.teardown(proc.pid, pgid, stop["why"])
        sv.say(f"ABORT: {stop['why']}; clean={ok}")
        return 3
    rc = proc.wait()
    ok = sv.teardown(proc.pid, pgid, f"command exited {rc}; sweeping recorded leftovers")
    sv.say(f"EXIT: command exited {rc}; clean={ok}")
    return rc

def stop(argv):
    state = argv[argv.index("--state") + 1]
    rows = open(os.path.join(state, "supervisor.id")).read().splitlines()
    pid, lstart = int(rows[0]), rows[1]
    argv = rows[2] if len(rows) > 2 else None
    t = ps_table()
    # identity = pid + start time + the exact argv ps showed when `run` began (older id files: argv must name groupwatch run)
    ok_argv = (t[pid][3] == argv) if (pid in t and argv is not None) else (pid in t and "groupwatch.py run" in t[pid][3])
    if pid not in t or t[pid][2] != lstart or not ok_argv:
        print(f"refusing: pid {pid} is not the recorded supervisor (gone, reused, or different argv)")
        return 1
    os.kill(pid, signal.SIGTERM)
    for _ in range(100):
        t = ps_table()
        if pid not in t or t[pid][2] != lstart:
            print(open(os.path.join(state, "groupwatch.log")).read().splitlines()[-1])
            return 0
        time.sleep(0.3)
    print("supervisor still running")
    return 1

if __name__ == "__main__":
    sys.exit({"run": run, "stop": stop}[sys.argv[1]](sys.argv[2:]))
