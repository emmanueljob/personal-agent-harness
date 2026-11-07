"""Build an isolated owner-installable Mac access prototype using the Swift toolchain."""
import argparse
from pathlib import Path
import plistlib
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
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
    with (contents / 'Info.plist').open('wb') as file:
        plistlib.dump({
            'CFBundleIdentifier': 'local.personalbrain.mac',
            'CFBundleName': 'Personal Brain', 'CFBundleDisplayName': 'Personal Brain',
            'CFBundleExecutable': 'PersonalBrain', 'CFBundlePackageType': 'APPL',
            'CFBundleShortVersionString': '0.2.0', 'CFBundleVersion': '2',
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
