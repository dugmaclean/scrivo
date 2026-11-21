"""Compile the Android app's resources and Java sources without Gradle, packaging or a phone.

Needs the Android SDK (platform android-34 and any build-tools with aapt2) and a JDK 17 javac.
The SDK is found from $ANDROID_HOME, then $ANDROID_SDK_ROOT, then ~/Android/Sdk; javac from
$JAVA_HOME/bin/javac, then PATH. Example:

    ANDROID_HOME=/path/to/android-sdk JAVA_HOME=/path/to/jdk-17 python3 tools/check_android.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / 'android/app/src/main'
PACKAGE = 'app.scrivo'
PLATFORM = 'android-34'


def find_sdk():
    for name in ('ANDROID_HOME', 'ANDROID_SDK_ROOT'):
        if os.environ.get(name):
            return Path(os.environ[name]).expanduser()
    return Path.home() / 'Android/Sdk'


def find_aapt2(sdk):
    """The newest installed build-tools that has aapt2."""
    def version(folder):
        return [int(part) if part.isdigit() else 0 for part in folder.name.split('.')]
    folders = sorted((sdk / 'build-tools').glob('*'), key=version, reverse=True)
    for folder in folders:
        if (folder / 'aapt2').is_file():
            return folder / 'aapt2'
    return None


def find_javac():
    if os.environ.get('JAVA_HOME'):
        javac = Path(os.environ['JAVA_HOME']).expanduser() / 'bin/javac'
        if javac.is_file():
            return javac
    found = shutil.which('javac')
    return Path(found) if found else None


def check():
    sdk = find_sdk()
    aapt = find_aapt2(sdk)
    jar = sdk / 'platforms' / PLATFORM / 'android.jar'
    javac = find_javac()
    missing = []
    if aapt is None:
        missing.append(f'aapt2 under {sdk}/build-tools (set ANDROID_HOME to your Android SDK)')
    if not jar.is_file():
        missing.append(f'{jar} (install the {PLATFORM} platform in your Android SDK)')
    if javac is None:
        missing.append('javac (set JAVA_HOME to a JDK 17 or put javac on PATH)')
    if missing:
        sys.exit('Missing: ' + '; '.join(missing))
    with tempfile.TemporaryDirectory(prefix='scrivo-android-') as folder:
        tmp = Path(folder)
        ET.register_namespace('android', 'http://schemas.android.com/apk/res/android')
        tree = ET.parse(APP / 'AndroidManifest.xml')
        tree.getroot().set('package', PACKAGE)
        tree.write(tmp / 'AndroidManifest.xml')
        generated_r = tmp / 'generated' / Path(*PACKAGE.split('.')) / 'R.java'
        commands = [
            [str(aapt), 'compile', '--dir', str(APP / 'res'), '-o', str(tmp / 'resources.zip')],
            [str(aapt), 'link', '-o', str(tmp / 'resources.ap_'), '--manifest', str(tmp / 'AndroidManifest.xml'),
             '-I', str(jar), '--java', str(tmp / 'generated'), str(tmp / 'resources.zip')],
            [str(javac), '-encoding', 'UTF-8', '-source', '17', '-target', '17', '-classpath', str(jar),
             '-d', str(tmp / 'classes'), *sorted(str(p) for p in (APP / 'java').rglob('*.java')), str(generated_r)]]
        rows = []
        for command in commands:
            result = subprocess.run(command, capture_output=True, text=True)
            rows.append({'tool': Path(command[0]).name, 'exit_code': result.returncode,
                         'output': result.stdout + result.stderr})
            if result.returncode:
                print(json.dumps({'results': rows}, indent=2))
                sys.exit(1)
        print(json.dumps({'scope': 'Resources and Java compile only. No Gradle build, APK or device check.',
                          'results': rows}, indent=2))


if __name__ == '__main__':
    check()
