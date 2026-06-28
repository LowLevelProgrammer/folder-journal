#!/usr/bin/env python3

import re
import sys
import json
import time
import hashlib
import subprocess
import os
import random
import tempfile
import platform
from pathlib import Path
from collections import defaultdict
from datetime import datetime
from time import perf_counter
import unicodedata

# ----------------------------
# GLOBALS
# ----------------------------

STORE = Path.home() / ".fj-store"
PROJECTS = STORE / "projects.json"
SNAPSHOTS = STORE / "snapshots"
BENCHMARKS = STORE / "benchmarks"
BENCH = False
BENCH_TIMES = {}


# ----------------------------
# BENCHMARKING
# ----------------------------


def bench_start():
    if not BENCH:
        return
    return perf_counter()


def bench_end(name, start):
    if not BENCH or start is None:
        return

    BENCH_TIMES[name] = BENCH_TIMES.get(name, 0) + (perf_counter() - start)


def cpu_name():
    with open("/proc/cpuinfo") as f:
        for line in f:
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()

    return "Unknown"


def bench_report(
    project: str,
    root: Path,
    files: int,
    total_bytes: int,
):
    lines = []

    now = datetime.now()

    lines.append("fj benchmark")
    lines.append("============")
    lines.append("")

    lines.append("Timestamp")
    lines.append("---------")
    lines.append(now.strftime("%Y-%m-%d %H:%M:%S"))
    lines.append("")

    lines.append("Project")
    lines.append("-------")
    lines.append(f"Name : {project}")
    lines.append(f"Root : {root}")
    lines.append("")

    lines.append("Dataset")
    lines.append("-------")
    lines.append(f"Files : {files:,}")
    lines.append(f"Size  : {total_bytes:,} bytes")
    lines.append("")

    lines.append("Timings")
    lines.append("-------")

    total = 0.0

    for name, value in BENCH_TIMES.items():
        total += value
        lines.append(f"{name:<16}{value:>8.3f} s")

    lines.append("-" * 24)
    lines.append(f"{'Total':<16}{total:>8.3f} s")
    lines.append("")

    hash_time = BENCH_TIMES["Hashing"]
    throughput = total_bytes / hash_time
    throughput_mib = throughput / (1024 * 1024)
    lines.append("Hash Throughput")
    lines.append("---------------")
    lines.append(f"{throughput_mib:.2f} MiB/s")
    lines.append("")

    lines.append("System")
    lines.append("------")
    lines.append(f"Python : {platform.python_version()}")
    lines.append(f"OS     : {platform.system()}")
    lines.append(f"Kernel : {platform.release()}")
    lines.append(f"Machine: {platform.machine()}")
    lines.append(f"CPU    : {cpu_name()}")

    text = "\n".join(lines) + "\n"

    print(text)

    BENCHMARKS.mkdir(parents=True, exist_ok=True)

    atomic_write(
        BENCHMARKS / f"{int(now.timestamp())}.txt",
        text,
    )


# ----------------------------
# HELPER
# ----------------------------

NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def validate_name(name):
    return bool(NAME_RE.fullmatch(name))


def safe_view_string(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def atomic_write(path: Path, data: str):
    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(path.parent))

    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())

        os.replace(tmp_path, path)

    except Exception:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise


# ----------------------------
# INIT STORE
# ----------------------------


def init_store():
    STORE.mkdir(parents=True, exist_ok=True)
    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    BENCHMARKS.mkdir(parents=True, exist_ok=True)

    if not PROJECTS.exists():
        PROJECTS.write_text("{}")

    print(f"Initialized fj store at {STORE}")


# ----------------------------
# PROJECT STORAGE
# ----------------------------


def load_projects():
    if not PROJECTS.exists():
        return {}
    return json.loads(PROJECTS.read_text())


def save_projects(data):
    atomic_write(PROJECTS, json.dumps(data, indent=2))


# ----------------------------
# PATH HELPERS
# ----------------------------


def normalize_path(p: str) -> str:
    return str(Path(p).expanduser().resolve(strict=False))


def to_relative(path: Path, root: Path):
    try:
        return str(path.relative_to(root))
    except ValueError:
        return None


# ----------------------------
# NAME GENERATION
# ----------------------------

ADJ = [
    "silent",
    "blue",
    "red",
    "ancient",
    "quiet",
    "golden",
    "bright",
    "cold",
    "wild",
    "dark",
]
NOUN = [
    "forest",
    "river",
    "mountain",
    "sky",
    "cloud",
    "harbor",
    "valley",
    "stone",
    "wind",
    "field",
]


def random_name():
    return f"{random.choice(ADJ)}-{random.choice(NOUN)}"


def generate_name(projects):
    for _ in range(50):
        n = random_name()
        if n not in projects:
            return n

    i = 2
    while True:
        n = f"{random_name()}-{i}"
        if n not in projects:
            return n
        i += 1


# ----------------------------
# ADD PROJECT
# ----------------------------


def add_project(path, name=None):
    if name and not validate_name(name):
        print("Invalid project name", file=sys.stderr)
        sys.exit(1)

    projects = load_projects()

    if not path:
        print("Usage: fj add <path> [--name name]", file=sys.stderr)
        sys.exit(1)

    path = normalize_path(path)

    if not name:
        name = generate_name(projects)

    if name in projects:
        print("Error: name already exists", file=sys.stderr)
        sys.exit(1)

    p = Path(path)

    if not p.exists():
        print("Path does not exist", file=sys.stderr)
        sys.exit(1)

    if not p.is_dir():
        print("Path is not a directory", file=sys.stderr)
        sys.exit(1)

    projects[name] = path
    save_projects(projects)

    (SNAPSHOTS / name).mkdir(parents=True, exist_ok=True)

    print(f"Created project: {name}")
    print(f"Root: {path}")


# ----------------------------
# SNAPSHOT ENGINE (ATOMIC)
# ----------------------------


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot(name):
    projects = load_projects()

    if name not in projects:
        print("Unknown project", file=sys.stderr)
        sys.exit(1)

    root = Path(projects[name]).resolve()
    ts = int(time.time())

    out_file = SNAPSHOTS / name / f"{ts}.jsonl"
    latest = SNAPSHOTS / name / "latest.jsonl"
    out_file.parent.mkdir(parents=True, exist_ok=True)

    t = bench_start()

    # Never snapshot fj's own metadata store.
    files = sorted(
        (f for f in root.rglob("*") if f.is_file() and STORE not in f.parents),
        key=lambda p: str(p),
    )
    total = len(files)

    bench_end("Traversal", t)

    print(f"Snapshot: {name}")
    print(f"Root: {root}")
    print(f"Files: {total}\n")

    t = bench_start()
    total_bytes = 0

    lines = []

    for i, f in enumerate(files, 1):
        try:
            rel = to_relative(f.resolve(), root)
            if rel is None:
                continue

            total_bytes += f.stat().st_size

            entry = {
                "snapshot": ts,
                "path": rel,
                "hash": sha256_file(f),
            }

            lines.append(json.dumps(entry))

        except Exception:
            pass

        if total:
            pct = int(i * 100 / total)
            print(f"\rProgress: {i}/{total} ({pct}%)", end="")

    bench_end("Hashing", t)

    t = bench_start()

    data = "\n".join(lines) + "\n"

    bench_end("Serialization", t)

    t = bench_start()
    # atomic snapshot write
    atomic_write(out_file, data)
    atomic_write(latest, data)

    bench_end("Writing", t)

    print(f"\nDone snapshot: {name} ({ts})")

    if BENCH:
        bench_report(project=name, root=root, files=total, total_bytes=total_bytes)


# ----------------------------
# LOG
# ----------------------------


def log_project(name):
    projects = load_projects()

    if name not in projects:
        print("Unknown project", file=sys.stderr)
        sys.exit(1)

    snapshot_path = SNAPSHOTS / name

    snapshots = [
        f for f in snapshot_path.iterdir() if f.is_file() and f.name != "latest.jsonl"
    ]

    if not snapshots:
        display("No snapshots.")
        return

    snapshots.sort(key=lambda p: int(p.stem), reverse=True)

    lines = []

    for f in snapshots:
        ts = int(f.stem)
        dt = datetime.fromtimestamp(ts)

        lines.append(f"{dt:%Y-%m-%d %H:%M:%S}  {ts}")

    display("\n".join(lines))


# ----------------------------
# VIEW
# ----------------------------


def view(name, out_file=None):
    projects = load_projects()

    if name not in projects:
        print("Unknown project", file=sys.stderr)
        sys.exit(1)

    latest = SNAPSHOTS / name / "latest.jsonl"
    if not latest.exists():
        print("No snapshot found", file=sys.stderr)
        sys.exit(1)

    root_path = Path(projects[name])
    root_name = root_path.name

    paths = []
    for line in latest.read_text().splitlines():
        obj = json.loads(line)
        paths.append(safe_view_string(obj["path"]))

    output = [f"[{root_name}]/"] + sorted(paths)
    output = "\n".join(output)

    if out_file:
        atomic_write(Path(out_file), output)
        print(f"View written to {out_file}")
        return

    display(output)


# ----------------------------
# TREE
# ----------------------------


def build_tree(paths):
    tree = lambda: defaultdict(tree)
    root = tree()

    for p in paths:
        node = root
        for part in Path(p).parts:
            node = node[part]

    return root


def render_tree(node, prefix=""):
    lines = []
    keys = sorted(node.keys())

    for i, k in enumerate(keys):
        last = i == len(keys) - 1
        connector = "└── " if last else "├── "
        lines.append(prefix + connector + k)

        extension = "    " if last else "│   "
        lines.extend(render_tree(node[k], prefix + extension))

    return lines


def load_snapshot_paths(name):
    latest = SNAPSHOTS / name / "latest.jsonl"
    if not latest.exists():
        return []

    return [
        safe_view_string(json.loads(l)["path"]) for l in latest.read_text().splitlines()
    ]


def tree(name, out_file=None):
    projects = load_projects()

    if name not in projects:
        print("Unknown project", file=sys.stderr)
        sys.exit(1)

    root_path = Path(projects[name])
    root_name = root_path.name

    paths = load_snapshot_paths(name)
    if not paths:
        print("No snapshot found", file=sys.stderr)
        sys.exit(1)

    t = build_tree(paths)
    lines = render_tree(t)

    output = [f"[{root_name}]/"] + lines
    output = "\n".join(output)

    if out_file:
        atomic_write(Path(out_file), output)
        print(f"Tree written to {out_file}")
        return

    display(output)


# ----------------------------
# DIFF
# ----------------------------


def load_snapshot(file):
    data = {}
    for line in Path(file).read_text().splitlines():
        obj = json.loads(line)
        data[obj["path"]] = obj["hash"]
    return data


def diff(name, a, b):
    A = SNAPSHOTS / name / f"{a}.jsonl"
    B = SNAPSHOTS / name / f"{b}.jsonl"

    if not A.exists() or not B.exists():
        print("Snapshot not found", file=sys.stderr)
        sys.exit(1)

    A = load_snapshot(A)
    B = load_snapshot(B)

    keys = set(A) | set(B)

    for k in sorted(keys):
        if k not in A:
            print("+", k)
        elif k not in B:
            print("-", k)
        elif A[k] != B[k]:
            print("*", k)


# ----------------------------
# OUTPUT HANDLER
# ----------------------------


def display(text):
    env = os.environ.copy()
    env["LESSCHARSET"] = "utf-8"

    subprocess.run(
        ["less", "-FRX"],
        input=text,
        text=True,
        env=env,
    )


# ----------------------------
# LIST
# ----------------------------


def list_projects(full=False):
    projects = load_projects()

    if full:
        for k, v in projects.items():
            print(f"{k} | {v}")
    else:
        for k in projects:
            print(k)


# ----------------------------
# USAGE
# ----------------------------


def usage():
    print("""
fj - filesystem snapshot tool (python version)

STORE:
  fj store init

PROJECTS:
  fj add <path> [--name name]
  fj list
  fj list -f

SNAPSHOTS:
  fj snapshot <name>

SNAPSHOTS HISTORY:
  fj log <name>

VIEW:
  fj view <name> [--out file]

TREE:
  fj tree <name> [--out file]

DIFF:
  fj diff <name> <a> <b>
""")


# ----------------------------
# CLI
# ----------------------------


def main():
    global BENCH

    if "--bench" in sys.argv:
        BENCH = True
        sys.argv.remove("--bench")

    if len(sys.argv) < 2:
        usage()
        return

    cmd = sys.argv[1]

    if cmd == "store":
        if len(sys.argv) > 2 and sys.argv[2] == "init":
            init_store()
        else:
            usage()

    elif cmd == "add":
        path = sys.argv[2] if len(sys.argv) > 2 else None
        name = None

        if "--name" in sys.argv:
            i = sys.argv.index("--name")

            if i + 1 >= len(sys.argv):
                print("--name requires a value", file=sys.stderr)
                sys.exit(1)

            name = sys.argv[i + 1]

            if name.startswith("-"):
                print("--name requires a value", file=sys.stderr)
                sys.exit(1)

        add_project(path, name)

    elif cmd == "snapshot":
        if len(sys.argv) != 3:
            print("Usage: fj snapshot <name>", file=sys.stderr)
            sys.exit(1)

        snapshot(sys.argv[2])

    elif cmd == "view":
        if len(sys.argv) < 3:
            print("Usage: fj view <name> [--out file]", file=sys.stderr)
            sys.exit(1)

        name = sys.argv[2]
        out = None

        if "--out" in sys.argv:
            i = sys.argv.index("--out")

            if i + 1 >= len(sys.argv):
                print("--out requires a value", file=sys.stderr)
                sys.exit(1)

            out = sys.argv[i + 1]

            if out.startswith("-"):
                print("--out requires a value", file=sys.stderr)
                sys.exit(1)

        view(name, out)

    elif cmd == "tree":
        if len(sys.argv) < 3:
            print("Usage: fj tree <name> [--out file]", file=sys.stderr)
            sys.exit(1)

        name = sys.argv[2]
        out = None

        if "--out" in sys.argv:
            i = sys.argv.index("--out")

            if i + 1 >= len(sys.argv):
                print("--out requires a value", file=sys.stderr)
                sys.exit(1)

            out = sys.argv[i + 1]

            if out.startswith("-"):
                print("--out requires a value", file=sys.stderr)
                sys.exit(1)

        tree(name, out)

    elif cmd == "list":
        list_projects("-f" in sys.argv)

    elif cmd == "diff":
        if len(sys.argv) != 5:
            print("Usage: fj diff <name> <a> <b>", file=sys.stderr)
            sys.exit(1)

        diff(sys.argv[2], sys.argv[3], sys.argv[4])

    elif cmd == "log":
        if len(sys.argv) != 3:
            print("Usage: fj log <name>", file=sys.stderr)
            sys.exit(1)

        log_project(sys.argv[2])

    else:
        usage()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted")
        sys.exit(130)
