"""Initialize isolated local retrieval indexes; originals are never moved."""
import os
import platform
import subprocess
import sys
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")
from harness.gbrain import GBrain
from harness.paths import brain_directory

VERSION = "0.60.530.0"


def main():
    binary = Path(os.getenv("GBRAIN_BINARY", str(ROOT / ".runtime/tools/gbrain/gbrain"))).resolve()
    if not binary.exists():
        target = {("Darwin", "arm64"): "gbrain-darwin-arm64", ("Linux", "x86_64"): "gbrain-linux-x64"}.get((platform.system(), platform.machine()))
        if not target:
            raise SystemExit("Install GBrain from its official repository and set GBRAIN_BINARY for this platform.")
        binary.parent.mkdir(parents=True, exist_ok=True)
        temporary = binary.with_suffix(".download")
        urllib.request.urlretrieve(f"https://github.com/garrytan/gbrain/releases/download/v{VERSION}/{target}", temporary)
        temporary.chmod(0o755)
        temporary.replace(binary)
    brain = GBrain(brain_directory(), binary=binary)
    for visibility in ("public", "private"):
        home = brain.index_root / visibility
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
        env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "USER", "LANG", "TMPDIR")}
        env["GBRAIN_HOME"] = str(home.resolve())
        commands = []
        if not (home / ".gbrain/config.json").exists():
            commands.append(["init", "--pglite", "--no-embedding", "--db-only", "--non-interactive", "--search-mode", "conservative", "--json"])
        commands += [["apply-migrations", "--yes", "--no-autopilot-install"],
                     ["config", "set", "facts.extraction_enabled", "false"],
                     ["config", "set", "memory.auto_writeback", "off"],
                     ["config", "set", "auto_chronicle", "false"]]
        for args in commands:
            result = subprocess.run([str(binary), *args], cwd=home, env=env, capture_output=True, text=True, timeout=180)
            if result.returncode:
                raise SystemExit(f"GBrain {visibility} setup failed at {args[0]}; exit {result.returncode}.")
        print(f"{visibility}: indexing local sources", flush=True)
        print(f"{visibility}: {brain.sync(visibility)} documents indexed", flush=True)
    print("Ready: local keyword search, separate databases, automatic capture and AI enrichment off.")
    print("Set BRAIN_BACKEND=gbrain in .env, then restart scripts/dev.py.")


if __name__ == "__main__":
    main()
