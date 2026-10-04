"""Launch the local console independently of legacy model initialization."""

import argparse
from pathlib import Path
import uvicorn

from easyrag.console.server import create_app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--cloud", action="store_true", help="Initial cloud default when no saved UI settings exist")
    parser.add_argument("--rca-root", type=Path, help="Server-only RCA repository directory")
    parser.add_argument("--rca-python", type=Path, help="Server-only RCA Python; defaults to the shared environment")
    args = parser.parse_args()
    uvicorn.run(create_app(allow_cloud=args.cloud, rca_root=args.rca_root, rca_python=args.rca_python),
                host="127.0.0.1", port=args.port, access_log=False)
