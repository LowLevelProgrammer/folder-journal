#!/usr/bin/env python3

import sys
import json
import time
import hashlib
import subprocess
import shutil
from pathlib import Path
from collections import defaultdict

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
# PATH NORMALIZATION (CANONICAL)
# ----------------------------

def normalize_path(p: str) -> str:
    return str(Path(p).expanduser().resolve(strict=False))

# ----------------------------
# NAME GENERATION
# ----------------------------

ADJ = ["silent", "blue", "red", "ancient", "quiet", "golden", "bright", "cold", "wild", "dark"]
NOUN = ["forest", "river", "mountain", "sky", "cloud", "harbor", "valley", "stone", "wind", "field"]

import random

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
    print(f"Path: {path}")

# ----------------------------
# SNAPSHOT ENGINE
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

    base = Path(projects[name])
    ts = int(time.time())

    out_file = SNAPSHOTS / name / f"{ts}.jsonl"
    out_file.parent.mkdir(parents=True, exist_ok=True)

    files = [f for f in base.rglob("*") if f.is_file()]
    total = len(files)

    print(f"Snapshot: {name}")
    print(f"Path: {base}")
    print(f"Files: {total}\n")

    with open(out_file, "w", encoding="utf-8") as out:
        for i, f in enumerate(files, 1):
            try:
                entry = {
                    "snapshot": ts,
                    "path": normalize_path(str(f)),
                    "hash": sha256_file(f),
                }
                out.write(json.dumps(entry) + "\n")
            except Exception:
                pass

            if total:
                pct = int(i * 100 / total)
                print(f"\rProgress: {i}/{total} ({pct}%)", end="")

    latest = SNAPSHOTS / name / "latest.jsonl"
    latest.write_text(out_file.read_text())

    print(f"\nDone snapshot: {name} ({ts})")

# ----------------------------
# VIEW
# ----------------------------

def view(name, out_file=None):
    latest = SNAPSHOTS / name / "latest.jsonl"

    if not latest.exists():
        print("No snapshot found")
        return

    paths = []
    for line in latest.read_text().splitlines():
        obj = json.loads(line)
        paths.append(obj["path"])

    output = "\n".join(sorted(paths))

    if out_file:
        Path(out_file).write_text(output, encoding="utf-8")
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

    return sorted(
        json.loads(l)["path"]
        for l in latest.read_text().splitlines()
    )

def tree(name, out_file=None):
    paths = load_snapshot_paths(name)

    if not paths:
        print("No snapshot found")
        return

    t = build_tree(paths)
    lines = render_tree(t)
    output = "\n".join(lines)

    if out_file:
        Path(out_file).write_text(output, encoding="utf-8")
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
        subprocess.run(["less"], input=text.encode())
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
