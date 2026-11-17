"""A gallery test run leaves no folders behind in the temp directory (see conftest.py)."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_a_partial_run_leaves_no_folders_in_tmp():
    own = Path(tempfile.mkdtemp(prefix="tmp-check-"))
    try:
        env = dict(os.environ, TMPDIR=str(own), PHOTOBOX_TMP_GUARD="strict")
        env.pop("DATA_DIR", None)
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                            "tests/test_boomerang_print_form.py", "tests/test_pairing_by_phone.py",
                            "tests/test_template_test_print.py"],
                           cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
        assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
        assert "TEMP GUARD" not in r.stdout
        left = sorted(p.name for p in own.iterdir())
        assert left == [], left
    finally:
        import shutil
        shutil.rmtree(own, ignore_errors=True)
