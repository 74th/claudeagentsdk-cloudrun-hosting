"""Validate a release file, show effective values, then run Terraform."""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from cas_hosting_adapter.release_config import load_release_config


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    config = load_release_config(args.config)
    variables = config.terraform_variables()
    print(
        f"retention_days={config.retention_days}; "
        "targets=Firestore sessions/runs/events and all GCS objects "
        "(workspace/transcript/temporary); deletion=asynchronous"
    )
    print(json.dumps(variables, sort_keys=True))
    with tempfile.NamedTemporaryFile(mode="w", suffix=".tfvars.json") as file:
        json.dump(variables, file)
        file.flush()
        base = ["terraform", "-chdir=terraform"]
        subprocess.run([*base, "init", "-backend=false"], check=True)
        subprocess.run([*base, "plan", f"-var-file={file.name}"], check=True)
        if args.apply:
            subprocess.run([*base, "apply", "-auto-approve", f"-var-file={file.name}"], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
