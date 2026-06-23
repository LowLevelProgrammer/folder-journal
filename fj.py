#!/usr/bin/env python3

import sys
import json
import time
import hashlib
import subprocess
import shutil
import os
import random
import tempfile
from pathlib import Path
from collections import defaultdict

import unicodedata

# ----------------------------
# HELPER
# ----------------------------

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
# STORE PATHS
# ----------------------------

STORE = Path.home() / ".fj-store"
PROJECTS = STORE / "projects.json"
SNAPSHOTS = STORE / "snapshots"

# ----------------------------
# INIT STORE
# ----------------------------

def init_store():
    STORE.mkdir(parents=True, exist_ok=True)
    SNAPSHOTS.mkdir(parents=True, exist_ok=True)

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
    PROJECTS.write_text(json.dumps(data, indent=2))

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

ADJ = ["silent", "blue", "red", "ancient", "quiet", "golden", "bright", "cold", "wild", "dark"]
NOUN = ["forest", "river", "mountain", "sky", "cloud", "harbor", "valley", "stone", "wind", "field"]

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
    projects = load_projects()

    if not path:
        print("Usage: fj add <path> [--name name]")
        return

    path = normalize_path(path)

    if not name:
        name = generate_name(projects)

    if name in projects:
        print("Error: name already exists")
        return

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
        print("Unknown project")
        return

    root = Path(projects[name]).resolve()
    ts = int(time.time())

    out_file = SNAPSHOTS / name / f"{ts}.jsonl"
    latest = SNAPSHOTS / name / "latest.jsonl"
    out_file.parent.mkdir(parents=True, exist_ok=True)

    files = [f for f in root.rglob("*") if f.is_file()]
    total = len(files)

    print(f"Snapshot: {name}")
    print(f"Root: {root}")
    print(f"Files: {total}\n")

    lines = []

    for i, f in enumerate(files, 1):
        try:
            rel = to_relative(f.resolve(), root)
            if rel is None:
                continue

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

    data = "\n".join(lines) + "\n"

    # atomic snapshot write
    atomic_write(out_file, data)
    atomic_write(latest, data)

    print(f"\nDone snapshot: {name} ({ts})")

# ----------------------------
# VIEW
# ----------------------------

def view(name, out_file=None):
    projects = load_projects()

    if name not in projects:
        print("Unknown project")
        return

    latest = SNAPSHOTS / name / "latest.jsonl"
    if not latest.exists():
        print("No snapshot found")
        return

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

    print_or_less(output)

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
        last = (i == len(keys) - 1)
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
        safe_view_string(json.loads(l)["path"])
        for l in latest.read_text().splitlines()
    ]

def tree(name, out_file=None):
    projects = load_projects()

    if name not in projects:
        print("Unknown project")
        return

    root_path = Path(projects[name])
    root_name = root_path.name

    paths = load_snapshot_paths(name)
    if not paths:
        print("No snapshot found")
        return

    t = build_tree(paths)
    lines = render_tree(t)

    output = [f"[{root_name}]/"] + lines
    output = "\n".join(output)

    if out_file:
        atomic_write(Path(out_file), output)
        print(f"Tree written to {out_file}")
        return

    print_or_less(output)

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
        print("Snapshot not found")
        return

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

def print_or_less(text):
    lines = text.count("\n")
    height = shutil.get_terminal_size((80, 20)).lines

    if lines > height * 2:
        env = os.environ.copy()
        env["LESSCHARSET"] = "utf-8"

        subprocess.run(
            ["less", "-R"],
            input=text,
            text=True,
            env=env
        )
    else:
        print(text)

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
            name = sys.argv[i + 1]

        add_project(path, name)

    elif cmd == "snapshot":
        snapshot(sys.argv[2])

    elif cmd == "view":
        name = sys.argv[2]
        out = None

        if "--out" in sys.argv:
            i = sys.argv.index("--out")
            out = sys.argv[i + 1]

        view(name, out)

    elif cmd == "tree":
        name = sys.argv[2]
        out = None

        if "--out" in sys.argv:
            i = sys.argv.index("--out")
            out = sys.argv[i + 1]

        tree(name, out)

    elif cmd == "list":
        list_projects("-f" in sys.argv)

    elif cmd == "diff":
        diff(sys.argv[2], sys.argv[3], sys.argv[4])

    else:
        usage()

if __name__ == "__main__":
    main()
