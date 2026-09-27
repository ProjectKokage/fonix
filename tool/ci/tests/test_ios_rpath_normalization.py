from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest


class IosRpathNormalizationTest(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "Xcode build phase uses a POSIX shell")
    def test_normalizes_known_toolchain_paths_and_rejects_unknown_or_duplicates(
        self,
    ) -> None:
        project = (
            Path(__file__).resolve().parents[3]
            / "example/ios/Runner.xcodeproj/project.pbxproj"
        )
        scripts = [
            json.loads(m[1])
            for m in re.finditer(
                r'shellScript = ("(?:\\.|[^"\\])*");', project.read_text()
            )
        ]
        scripts = [script for script in scripts if "rpath_count()" in script]
        self.assertEqual(len(scripts), 1)
        final = ["/usr/lib/swift", "@executable_path/Frameworks"]
        for extra, succeeds in (
            ([], True),
            (["@loader_path"], True),
            (["@loader_path", "@loader_path"], False),
            (["/unexpected/path"], False),
        ):
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / "Runner").write_bytes(b"fixture")
                paths = root / "paths.json"
                paths.write_text(
                    json.dumps([*final, str(root / "PackageFrameworks"), *extra])
                )
                otool = root / "otool"
                otool.write_text(
                    f"#!{sys.executable}\n"
                    + """import json, os
from pathlib import Path
for path in json.loads(Path(os.environ['FONIX_RPATH_FIXTURE']).read_text()):
    print('cmd LC_RPATH\\n    path '+path+' (offset 12)')
"""
                )
                install = root / "install_name_tool"
                install.write_text(
                    f"#!{sys.executable}\n"
                    + """import json, os, sys
from pathlib import Path
assert sys.argv[1] == '-delete_rpath'
p = Path(os.environ['FONIX_RPATH_FIXTURE'])
paths = json.loads(p.read_text())
paths.remove(sys.argv[2])
p.write_text(json.dumps(paths))
"""
                )
                for tool in (otool, install):
                    tool.chmod(0o700)
                script = (
                    scripts[0]
                    .replace("/usr/bin/otool", str(otool))
                    .replace("/usr/bin/install_name_tool", str(install))
                )
                result = subprocess.run(
                    ["/bin/sh", "-c", script],
                    capture_output=True,
                    text=True,
                    timeout=20,
                    env={
                        **os.environ,
                        "CONFIGURATION": "Debug",
                        "PLATFORM_NAME": "iphonesimulator",
                        "TARGET_BUILD_DIR": str(root),
                        "EXECUTABLE_PATH": "Runner",
                        "FONIX_RPATH_FIXTURE": str(paths),
                    },
                )
                self.assertEqual(result.returncode == 0, succeeds, result.stderr)
                if succeeds:
                    self.assertEqual(json.loads(paths.read_text()), final)
