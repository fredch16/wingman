"""Exercise deployment and rollback with isolated files and stubbed services."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(
    all(shutil.which(tool) for tool in ("bash", "rsync", "tar")),
    "Deployment checks require bash, rsync, and tar",
)
class PiDeploymentTests(unittest.TestCase):
    def check_deployment(self, fail_health_check):
        script = (Path(__file__).resolve().parents[1] / "deploy/deploy-pi.sh").read_text()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, target, backup, commands = (
                root / name for name in ("source", "target", "backup", "bin")
            )
            commands.mkdir()
            for location, content in ((source, "new"), (target, "old")):
                for directory in ("src/wingman/static", "deploy", "tests", ".venv/bin"):
                    (location / directory).mkdir(parents=True, exist_ok=True)
                (location / "src/wingman/static/app.js").write_text(content)
                for name in (
                    "pyproject.toml", "requirements.txt", "README.md",
                    "app.py", "fetch_comments.py", "wingman",
                ):
                    (location / name).write_text(content)
            (source / "src/new-file").write_text("new")
            (target / "src/old-file").write_text("old")
            (target / ".env").write_text("preserve-env")
            (target / "creator.md").write_text("preserve-creator")
            (target / "state").mkdir()
            (target / "state/live.db").write_text("preserve-db")
            (target / ".deployed-revision").write_text("previous")
            python = target / ".venv/bin/python"
            python.write_text("#!/bin/sh\nexit 0\n")
            python.chmod(0o755)
            stubs = {
                "git": "echo 1234567890abcdef",
                "flock": "exit 0",
                "sudo": "exit 0",
                "systemctl": "exit 0",
                "curl": '''case "$*" in
                  *5001*) printf 404 ;;
                  *static/app.js*) cat "$TEST_TARGET/src/wingman/static/app.js" ;;
                  *) if [ "$FAIL_HEALTH" = yes ]; then exit 22; fi ;;
                esac''',
            }
            for name, body in stubs.items():
                executable = commands / name
                executable.write_text("#!/bin/sh\n" + body + "\n")
                executable.chmod(0o755)
            deploy = source / "deploy/deploy-pi.sh"
            deploy.write_text(
                script.replace("/home/fred/wingman-hackathon", str(target))
                .replace("/home/fred/wingman-deploy-backups", str(backup))
            )
            result = subprocess.run(
                ["bash", str(deploy)],
                env=dict(
                    os.environ,
                    PATH=str(commands) + os.pathsep + os.environ["PATH"],
                    TEST_TARGET=str(target),
                    FAIL_HEALTH="yes" if fail_health_check else "no",
                ),
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, int(fail_health_check), result.stdout + result.stderr)
            self.assertEqual(
                (target / "src/wingman/static/app.js").read_text(),
                "old" if fail_health_check else "new",
            )
            self.assertEqual((target / "src/old-file").exists(), fail_health_check)
            self.assertEqual((target / "src/new-file").exists(), not fail_health_check)
            self.assertEqual((target / ".env").read_text(), "preserve-env")
            self.assertEqual((target / "creator.md").read_text(), "preserve-creator")
            self.assertEqual((target / "state/live.db").read_text(), "preserve-db")
            self.assertEqual(
                (target / ".deployed-revision").read_text().strip(),
                "previous" if fail_health_check else "1234567890abcdef",
            )

    def test_success_updates_code_and_preserves_local_state(self):
        self.check_deployment(fail_health_check=False)

    def test_failed_health_check_restores_code_and_revision(self):
        self.check_deployment(fail_health_check=True)
