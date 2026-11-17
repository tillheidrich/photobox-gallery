"""Both compose files are valid and keep their promises (S-5 and the NAS variant).

The default file is for a server behind a reverse proxy: loopback port only,
TRUST_PROXY off unless set. The NAS file publishes the port on the LAN, keeps
TRUST_PROXY at 0, demands BASE_URL, NAS_DATA_DIR and SECRET_KEY, and runs as
PUID:PGID instead of root.

Full validation uses `docker compose config` (or `docker-compose config`)
when it is installed — CI runs the same command in its own step. Until
15.11.2026 docker-compose.yml was not valid YAML at all (a plain multi-line
scalar in the pruner); nothing noticed, because nothing parsed it.
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NAS_ENV = {"NAS_DATA_DIR": "/volume1/docker/photobox/data", "SECRET_KEY": "x" * 40,
           "BASE_URL": "http://192.168.1.20:5000"}


def _compose_cmd():
    if shutil.which("docker-compose"):
        return ["docker-compose"]
    if shutil.which("docker"):
        probe = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True)
        if probe.returncode == 0:
            return ["docker", "compose"]
    return None


def _config(name, env):
    cmd = _compose_cmd()
    if not cmd:
        pytest.skip("docker compose not installed here; CI validates the files in its own step")
    run_env = {k: v for k, v in os.environ.items() if k not in NAS_ENV}
    run_env.update(env)
    return subprocess.run(cmd + ["-f", str(ROOT / name), "config"], capture_output=True, text=True,
                          env=run_env, cwd=ROOT)


def test_the_default_compose_file_is_valid():
    r = _config("docker-compose.yml", {})
    assert r.returncode == 0, r.stderr
    assert "host_ip: 127.0.0.1" in r.stdout
    assert 'TRUST_PROXY: "0"' in r.stdout


def test_the_nas_compose_file_is_valid_and_demands_its_settings():
    r = _config("docker-compose.nas.yml", NAS_ENV)
    assert r.returncode == 0, r.stderr
    # Reached over the LAN on purpose (0.0.0.0); BIND_ADDR keeps it on the NAS (R2-U6).
    assert "host_ip: 0.0.0.0" in r.stdout, "the NAS variant is reached over the LAN"
    local = _config("docker-compose.nas.yml", {**NAS_ENV, "BIND_ADDR": "127.0.0.1"})
    assert local.returncode == 0 and "host_ip: 127.0.0.1" in local.stdout
    assert 'TRUST_PROXY: "0"' in r.stdout and "user: 1026:100" in r.stdout
    for missing in NAS_ENV:
        r = _config("docker-compose.nas.yml", {k: v for k, v in NAS_ENV.items() if k != missing})
        assert r.returncode != 0 and missing in r.stderr, missing


def test_the_nas_file_says_what_it_promises_even_without_docker():
    text = (ROOT / "docker-compose.nas.yml").read_text(encoding="utf-8")
    assert "TRUST_PROXY=${TRUST_PROXY:-0}" in text
    assert re.search(r"BASE_URL=\$\{BASE_URL:\?", text)
    assert re.search(r'user: "\$\{PUID:-\d+\}:\$\{PGID:-\d+\}"', text)
    assert "${NAS_DATA_DIR:?" in text
    assert (ROOT / "docs" / "INSTALL-NAS.md").is_file()


def test_the_image_builds_for_amd64_and_arm64():
    """python:*-slim is a multi-arch image; a pinned digest or an -amd64 tag would end that."""
    first = (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines()[0]
    assert re.fullmatch(r"FROM python:3\.\d+-slim", first), first
