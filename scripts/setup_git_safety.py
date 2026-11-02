"""Install the pinned official scanner and activate this checkout's Git hooks."""
import hashlib
import io
import platform
import subprocess
import tarfile
import urllib.request
from pathlib import Path

VERSION = '8.30.1'
RELEASES = {
    ('Darwin','arm64'): ('darwin_arm64','b40ab0ae55c505963e365f271a8d3846efbc170aa17f2607f13df610a9aeb6a5'),
    ('Darwin','x86_64'): ('darwin_x64','dfe101a4db2255fc85120ac7f3d25e4342c3c20cf749f2c20a18081af1952709'),
    ('Linux','x86_64'): ('linux_x64','551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb'),
    ('Linux','aarch64'): ('linux_arm64','e4a487ee7ccd7d3a7f7ec08657610aa3606637dab924210b3aee62570fb4b080'),
}


def main():
    root = Path(__file__).resolve().parents[1]
    target, checksum = RELEASES[(platform.system(), platform.machine())]
    url = f'https://github.com/gitleaks/gitleaks/releases/download/v{VERSION}/gitleaks_{VERSION}_{target}.tar.gz'
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != checksum:
        raise SystemExit('Scanner download checksum mismatch; hooks were not activated.')
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        binary = archive.extractfile('gitleaks').read()
    dest = root / '.runtime/tools/gitleaks/gitleaks'
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(binary)
    dest.chmod(0o755)
    for name in ('pre-commit', 'pre-push'):
        (root / '.githooks' / name).chmod(0o755)
    subprocess.run(['git','config','--local','core.hooksPath','.githooks'], cwd=root, check=True)
    print(f'Gitleaks {VERSION}: checksum verified. Pre-commit and pre-push hooks activated.')


if __name__ == '__main__':
    main()
