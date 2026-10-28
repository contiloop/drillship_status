"""Install the user's macOS queue consumer; no Codex desktop scheduler needed."""
from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

LABEL = "com.contiloop.drillship-news-worker"


def launch_agent(root: Path, executable: str, paths: list[str]) -> dict:
    return {"Label": LABEL,
            "ProgramArguments": [executable, "-u", "-m", "automation.news_worker", "--root", str(root)],
            "WorkingDirectory": str(root), "StartInterval": 300, "RunAtLoad": True,
            "ProcessType": "Background", "ThrottleInterval": 60,
            "EnvironmentVariables": {"PATH": ":".join(dict.fromkeys(paths))},
            "StandardOutPath": str(root / ".cache/news-worker.log"),
            "StandardErrorPath": str(root / ".cache/news-worker-error.log")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("this installer requires macOS launchd")
    root = args.root.resolve()
    paths = []
    for tool in ("codex", "node", "npm", "git"):
        executable = shutil.which(tool)
        if not executable:
            parser.error(f"required executable not found: {tool}")
        paths.append(str(Path(executable).parent))
    paths.extend(os.defpath.split(":"))
    (root / ".cache").mkdir(exist_ok=True)
    target = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and plistlib.loads(target.read_bytes()).get("Label") != LABEL:
        raise RuntimeError("refusing to overwrite an unrelated launch agent")
    domain = f"gui/{os.getuid()}"
    service = f"{domain}/{LABEL}"
    if subprocess.run(["launchctl", "print", service], capture_output=True).returncode == 0:
        subprocess.run(["launchctl", "bootout", service], check=True)
    target.write_bytes(plistlib.dumps(launch_agent(root, sys.executable, paths)))
    target.chmod(0o600)
    subprocess.run(["launchctl", "bootstrap", domain, str(target)], check=True)
    subprocess.run(["launchctl", "print", service], check=True, capture_output=True)
    print(f"Installed compatible v1/v2 worker {service}: server queue checked every 5 minutes, Codex only for pending work.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
