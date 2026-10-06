"""Build private server archives without docs, credentials or local tool environments."""

import argparse
from pathlib import Path
import tarfile


SKIP_PARTS = {".git", "docs", "node_modules", "__pycache__", ".idea", ".vscode", ".venv",
              "venv", "env", "site-packages", ".pytest_cache", "test-results", "playwright-report"}
SKIP_OUTPUT_PREFIXES = ("cleanup-", "release-", "console-qa", "optional_runtime")


def permitted(relative):
    if SKIP_PARTS.intersection(relative.parts):
        return False
    if any(part.startswith((".venv", "venv")) for part in relative.parts):
        return False
    name = relative.name
    if name.startswith(".env") and name != ".env.example":
        return False
    if (name.startswith("private-") or name in {"service-settings.json", "credentials.json", "secrets.json",
                                               "id_rsa", "id_ed25519", "api-key.txt", "api_key.txt"}
            or name.endswith((".pem", ".key", ".lock", ".pyc", ".pyo"))):
        return False
    if len(relative.parts) > 1 and relative.parts[0] == "outputs" and relative.parts[1].startswith(SKIP_OUTPUT_PREFIXES):
        return False
    return True


def archive(root, output, entries):
    count = 0
    with tarfile.open(output, "w:gz", compresslevel=1) as tar:
        for entry in entries:
            path = root / entry
            files = sorted(path.rglob("*")) if path.is_dir() else [path]
            for file in files:
                relative = file.relative_to(root)
                if file.is_file() and not file.is_symlink() and permitted(relative):
                    tar.add(file, arcname=relative.as_posix(), recursive=False)
                    count += 1
    print(f"{output.name}: {count} files, {output.stat().st_size:,} bytes")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rca-root", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    destination = args.output.resolve()
    if destination.is_relative_to(root) or destination.is_relative_to(args.rca_root.resolve()):
        parser.error("--output must be outside both project directories")
    args.output.mkdir(parents=True, exist_ok=True)
    source_entries = [p.name for p in root.iterdir() if p.name not in {"data", "knowledge", "outputs"}]
    archive(root, args.output / "source.tar.gz", source_entries)
    archive(root, args.output / "state.tar.gz", ["data", "knowledge", "outputs"])
    # RCA stays a separate project. The full vendor code and license are preserved.
    archive(args.rca_root, args.output / "rca.tar.gz", ["src", "scripts", "configs", "schemas", "vendor", "README.md", "pyproject.toml"])


if __name__ == "__main__":
    main()
