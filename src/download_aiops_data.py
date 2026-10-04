"""Fetch the five pinned public dataset archives, without models or eReader."""

import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

from prepare_aiops_corpus import ROOT, sha256_file


def download(source_dir, lock_path):
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    source_dir.mkdir(parents=True, exist_ok=True)
    for name, spec in lock["files"].items():
        target = source_dir / name
        if target.is_file() and target.stat().st_size == spec["size"] and sha256_file(target) == spec["sha256"]:
            print(f"Verified existing {name}", flush=True)
            continue
        if target.exists():
            with target.open("rb") as stream:
                is_pointer = stream.read(100).startswith(b"version https://git-lfs.github.com/spec/v1")
            if not is_pointer:
                raise ValueError(f"unexpected existing file; not overwritten: {name}")
        # Both public sources support immutable resolve URLs; labels stay in the acquisition lock.
        url = f"{lock['source_url']}/resolve/{lock['revision']}/{spec.get('source_path', name)}"
        partial = target.with_suffix(target.suffix + ".download-part")
        digest, size, next_progress = hashlib.sha256(), 0, 16 * 1024 * 1024
        with urllib.request.urlopen(url, timeout=45) as response, partial.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
                digest.update(chunk)
                size += len(chunk)
                if size > spec["size"]:
                    raise ValueError(f"download exceeds expected size: {name}")
                if size >= next_progress:
                    print(f"{name}: {size}/{spec['size']} bytes", flush=True)
                    next_progress += 16 * 1024 * 1024
        if size != spec["size"] or digest.hexdigest() != spec["sha256"]:
            raise ValueError(f"download checksum/size mismatch: {name}")
        partial.replace(target)
        print(f"Downloaded and verified {name}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=ROOT / "data/external/aiops2024-challenge-dataset")
    parser.add_argument("--source-lock", type=Path, default=ROOT / "src/configs/data_sources/aiops2024.json")
    args = parser.parse_args()
    download(args.source_dir, args.source_lock)
