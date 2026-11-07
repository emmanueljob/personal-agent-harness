"""Build an isolated owner-installable Mac access prototype using the Swift toolchain."""
import argparse
import hashlib
import shutil
import urllib.request
from pathlib import Path
import plistlib
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gbrain-binary', type=Path, help='Existing pinned GBrain binary; hash is verified before bundling')
    parser.add_argument('--launch', action='store_true')
    parser.add_argument('--test', action='store_true')
    args = parser.parse_args()
    source = Path(__file__).resolve().parent
    build = source.parents[1] / '.runtime' / 'personal-brain-macos'
    app = build / 'Personal Brain.app'
    contents = app / 'Contents'
    executable = contents / 'MacOS' / 'PersonalBrain'
    executable.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['xcrun', 'swiftc', '-swift-version', '5', '-parse-as-library',
                    '-target', 'arm64-apple-macosx14.0', '-module-cache-path', str(build / 'module-cache'),
                    *map(str, sorted((source / 'Sources').glob('*.swift'))),
                    '-framework', 'SwiftUI', '-framework', 'AppKit', '-framework', 'EventKit',
                    '-lsqlite3', '-o', str(executable)], check=True)
    resources = contents / 'Resources'
    resources.mkdir(parents=True, exist_ok=True)
    for worker in ('memory_worker.py', 'dream_cycle.py'):
        shutil.copy2(source / 'Worker' / worker, resources / worker)
    dependencies = [
        ('pytypedstream.whl', 'https://files.pythonhosted.org/packages/0d/1a/dbbbad9690c24506aec4571bc6613a5625b163b1607777109c9dabdbec07/pytypedstream-0.1.0-py3-none-any.whl', '499920d4cb8bec8fd9d9cdd4c6312765eb418c398c10371ab1c9c0051104d278'),
        ('pytypedstream-source.tar.gz', 'https://files.pythonhosted.org/packages/47/d2/4632904c21890b1f397e6e9ef823b216f6f0a3a767897b092f592f16b90e/pytypedstream-0.1.0.tar.gz', 'c3b01790e667189966e98fcfeb638cc0541358ea7348745619fe58e3e8c9fd51'),
        ('gbrain', 'https://github.com/garrytan/gbrain/releases/download/v0.60.530.0/gbrain-darwin-arm64', '8ca750fbe7fad770a23d873ffe898170d4746bc4044814b5be78b974b5196317'),
    ]
    for name, url, digest in dependencies:
        target = resources / name
        if name == 'gbrain' and args.gbrain_binary:
            data = args.gbrain_binary.read_bytes()
        elif target.exists():
            data = target.read_bytes()
        else:
            with urllib.request.urlopen(url, timeout=120) as response:
                data = response.read()
        if hashlib.sha256(data).hexdigest() != digest:
            raise SystemExit(f'{name}: checksum mismatch; refusing to bundle.')
        target.write_bytes(data)
        if name == 'gbrain':
            target.chmod(0o755)
    shutil.copy2(source / 'THIRD-PARTY.md', resources / 'THIRD-PARTY.md')
    with (contents / 'Info.plist').open('wb') as file:
        plistlib.dump({
            'CFBundleIdentifier': 'local.personalbrain.mac',
            'CFBundleName': 'Personal Brain', 'CFBundleDisplayName': 'Personal Brain',
            'CFBundleExecutable': 'PersonalBrain', 'CFBundlePackageType': 'APPL',
            'CFBundleShortVersionString': '0.4.0', 'CFBundleVersion': '4',
            'LSMinimumSystemVersion': '14.0', 'NSHighResolutionCapable': True,
            'NSCalendarsFullAccessUsageDescription': 'Read calendars you select to import events into your local personal brain. This app does not modify your calendars.',
        }, file)
    # A designated identifier keeps the ad-hoc development identity stable across builds.
    # Developer ID signing/notarization is a later distribution gate.
    subprocess.run(['codesign', '--force', '--sign', '-', '--identifier', 'local.personalbrain.mac',
                    '--requirements', '=designated => identifier "local.personalbrain.mac"', str(app)], check=True)
    subprocess.run(['codesign', '--verify', '--strict', str(app)], check=True)
    if args.test:
        subprocess.run([str(executable), '--self-test'], check=True)
    if args.launch:
        subprocess.run(['open', str(app)], check=True)
    print(app)


if __name__ == '__main__':
    main()
