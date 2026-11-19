"""Repository hygiene gates (plan task 1.5, gate G1/G2)."""
import os
import re

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
APP_ROOT = os.path.dirname(os.path.dirname(HERE))          # the project (pyproject.toml, src/, tests/)
REPO_ROOT = APP_ROOT
TEXT_EXT = (".py", ".md", ".txt", ".js", ".html", ".json", ".sample", ".toml", ".yml", ".yaml", ".bat", ".sh")
SKIP_DIRS = {".git", "venv", ".venv", "node_modules", "__pycache__", "instance", ".pytest_cache", "fixtures",
             "test-results", ".ruff_cache", ".hypothesis"}


def _files(root):
    for d, dirs, files in os.walk(root):
        # skip any virtual environment (e.g. .venv-verify created by scripts/verify.sh): third-party files
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS and not os.path.exists(os.path.join(d, x, "pyvenv.cfg"))]
        for f in files:
            if f.endswith(TEXT_EXT) or f.startswith(".env"):
                yield os.path.join(d, f)


def test_no_hardcoded_api_keys():                                          # SEC-02
    pattern = re.compile(r"(api_?key\s*[=:]\s*['\"][0-9a-f]{32,}['\"])|\b[0-9a-f]{64}\b", re.I)
    hits = []
    for p in _files(REPO_ROOT):
        with open(p, errors="ignore") as f:
            hits += [f"{p}:{i}" for i, line in enumerate(f, 1) if pattern.search(line)]
    assert hits == []


def test_licence_is_consistent():                                          # D6
    with open(os.path.join(REPO_ROOT, "LICENSE")) as f:
        assert "GNU AFFERO GENERAL PUBLIC LICENSE" in f.read()
    for p in _files(REPO_ROOT):
        with open(p, errors="ignore") as f:
            txt = f.read()
        assert not re.search(r"licen[sc]ing\s*-\s*MIT", txt, re.I), p


def test_core_never_creates_tables_dynamically():                          # G2: dynamic-table anti-pattern
    core = os.path.join(APP_ROOT, "src", "tradealpha")
    offenders = []
    for p in _files(core):
        if not p.endswith(".py") or "migrations" in p:
            continue
        with open(p) as f:
            src = f.read()
        if re.search(r"__table__\.create|type\([^)]*db\.Model|Base\.metadata\.create_all", src):
            offenders.append(p)
    assert offenders == []


@pytest.mark.parametrize("req", ["requirements.txt", "requirements-dev.txt"])
def test_requirements_pinned(req):
    with open(os.path.join(APP_ROOT, req)) as f:
        lines = f.read().splitlines()
    for line in lines:
        line = line.strip()
        if line and not line.startswith(("#", "-r")):
            assert "==" in line, line


def test_run_py_secure_defaults():                                         # SEC-01
    with open(os.path.join(APP_ROOT, "src", "tradealpha", "apps", "serve.py")) as f:
        src = f.read()
    assert 'env("HOST", "127.0.0.1")' in src
    assert "debug=True" not in src and "use_reloader=False" in src
