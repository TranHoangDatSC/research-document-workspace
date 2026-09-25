"""Apply reviewed file moves with content guards, backup and reversible rollback.
Standard-library Python 3.11+, Windows/Linux. Never reads .env or touches Docker.
"""

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import sys
from uuid import uuid4

PACKAGE = Path(__file__).resolve().parent


def content_hash(path):
    raw = path.read_bytes()
    try:
        raw = raw.decode("utf-8-sig").replace("\r\n", "\n").encode("utf-8")
    except UnicodeError:
        pass
    return hashlib.sha256(raw).hexdigest()


def byte_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_path(root, relative):
    p = root / relative
    resolved = p.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise RuntimeError("Path outside project: " + relative)
    if p.is_symlink():
        raise RuntimeError("Symlink refused: " + relative)
    return p


def read_manifest():
    return json.loads((PACKAGE / "manifest.json").read_text(encoding="utf-8"))


def check(project, manifest):
    for relative, expected in manifest["expected"].items():
        path = safe_path(project, relative)
        if not path.is_file() or content_hash(path) != expected:
            raise RuntimeError(
                "Source differs from the reviewed ZIP: "
                + relative
                + ". No changes made. Send the current file before applying."
            )
    for relative, expected in manifest["payload"].items():
        source = safe_path(PACKAGE / "payload", relative)
        if not source.is_file() or byte_hash(source) != expected:
            raise RuntimeError("Package file damaged: " + relative)
        destination = safe_path(project, relative)
        if destination.exists() and relative not in manifest["expected"]:
            raise RuntimeError(
                "New destination already exists: " + relative + ". No changes made."
            )
    active_moves = []
    for move in manifest["moves"]:
        source, destination = safe_path(project, move["from"]), safe_path(
            project, move["to"]
        )
        if destination.exists():
            raise RuntimeError(
                "Move destination already exists: " + move["to"] + ". No changes made."
            )
        if source.exists():
            if not source.is_file():
                raise RuntimeError("Not a regular file: " + move["from"])
            active_moves.append(move)
    return active_moves


def prune_empty(parent, root):
    while parent != root and parent.is_relative_to(root):
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent


def restore(backup, state, guard=True):
    project = Path(state["project"]).resolve()
    if guard:
        for relative, expected in state["after"].items():
            target = safe_path(project, relative)
            actual = byte_hash(target) if target.is_file() else None
            if actual != expected:
                if relative.startswith("artifacts/") and target.is_file():
                    preserved = backup / "after-run-artifacts" / relative
                    preserved.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, preserved)
                elif relative.startswith("artifacts/") and actual is None:
                    pass
                else:
                    raise RuntimeError(
                        "File changed since Day 3 update: "
                        + relative
                        + ". Rollback stopped to preserve your edits."
                    )
    # Verify every backup before changing anything.
    for relative, original in state["before"].items():
        if original is not None:
            saved = safe_path(backup / "files", relative)
            if not saved.is_file() or byte_hash(saved) != original:
                raise RuntimeError("Missing/corrupt backup: " + relative)
    for relative, original in state["before"].items():
        target = safe_path(project, relative)
        if original is None:
            if target.exists():
                target.unlink()
                prune_empty(target.parent, project)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup / "files" / relative, target)
    state["rolled_back"] = True
    (backup / "rollback.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    print("Rollback complete. Source files restored; .env and volumes untouched.")
    print(
        "Rebuild web: docker compose up -d --build --no-deps --wait --wait-timeout 180 web"
    )


def apply(project, manifest, moves):
    touched = set(manifest["payload"]) | set(manifest["remove_old"])
    for move in moves:
        touched.update([move["from"], move["to"]])
    backup = project.parent / (
        project.name
        + "-day3-backup-"
        + datetime.now().strftime("%Y%m%d-%H%M%S")
        + "-"
        + uuid4().hex[:8]
    )
    backup.mkdir()
    before = {}
    for relative in sorted(touched):
        path = safe_path(project, relative)
        before[relative] = byte_hash(path) if path.is_file() else None
        if path.is_file():
            target = backup / "files" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    state = {
        "project": str(project),
        "before": before,
        "after": {},
        "rolled_back": False,
    }
    (backup / "rollback.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    shutil.copy2(__file__, backup / "apply_day3.py")
    try:
        for relative in manifest["payload"]:
            target = safe_path(project, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(PACKAGE / "payload" / relative, target)
        for move in moves:
            old, new = safe_path(project, move["from"]), safe_path(project, move["to"])
            new.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(old, new)
            old.unlink()
        for relative in manifest["remove_old"]:
            old = safe_path(project, relative)
            if old.exists():
                old.unlink()
                prune_empty(old.parent, project)
        state["after"] = {
            r: byte_hash(project / r) if (project / r).is_file() else None
            for r in sorted(touched)
        }
        (backup / "rollback.json").write_text(
            json.dumps(state, indent=2), encoding="utf-8"
        )
    except BaseException:
        restore(backup, state, guard=False)
        raise
    print("Day 3 source applied. Existing data and test state untouched.")
    print("BACKUP: " + str(backup))
    print("Rollback command:")
    print(
        'python "'
        + str(backup / "apply_day3.py")
        + '" --rollback "'
        + str(backup)
        + '"'
    )
    print(
        "No Docker commands were run. Follow DAY3-GUIDE.md to rebuild and verify."
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default=".")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--apply", action="store_true")
    action.add_argument("--rollback", metavar="BACKUP_DIRECTORY")
    args = parser.parse_args()
    if args.rollback:
        backup = Path(args.rollback).resolve()
        state = json.loads((backup / "rollback.json").read_text(encoding="utf-8"))
        if state["rolled_back"]:
            raise RuntimeError("This backup has already been restored.")
        restore(backup, state)
        return
    project = Path(args.project).resolve()
    manifest = read_manifest()
    moves = check(project, manifest)
    print("Reviewed source matches. Files to write: " + str(len(manifest["payload"])))
    for old in manifest["remove_old"]:
        print("REPLACE OLD MODULE: " + old)
    for move in moves:
        print("MOVE: " + move["from"] + " -> " + move["to"])
    if args.apply:
        apply(project, manifest, moves)
    else:
        print("CHECK: PASS. No files changed.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("STOP: " + str(exc), file=sys.stderr)
        sys.exit(1)
