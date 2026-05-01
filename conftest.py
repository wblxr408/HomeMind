"""Pytest configuration: add project root to sys.path and isolate runtime data."""
import os
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

project_root = Path(__file__).parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

_pytest_kb_paths = set()
_pytest_kb_backup = Path(tempfile.gettempdir()) / f"homemind_pytest_kb_backup_{os.getpid()}_session.enc"
os.environ.setdefault("HOMEMIND_KB_BACKUP_PATH", str(_pytest_kb_backup))
os.environ.setdefault("HOMEMIND_DISABLE_BACKGROUND_THREADS", "1")
_pytest_kb_paths.add(_pytest_kb_backup)


def _safe_unlink(path: Path):
    try:
        path.unlink(missing_ok=True)
    except PermissionError:
        pass


_safe_unlink(_pytest_kb_backup)


def pytest_sessionfinish(session, exitstatus):
    for path in list(_pytest_kb_paths):
        _safe_unlink(path)


@pytest.fixture(autouse=True)
def _isolate_kb_backup():
    backup_path = Path(tempfile.gettempdir()) / f"homemind_pytest_kb_backup_{os.getpid()}_{uuid.uuid4().hex}.enc"
    _pytest_kb_paths.add(backup_path)
    os.environ["HOMEMIND_KB_BACKUP_PATH"] = str(backup_path)
    _safe_unlink(backup_path)
    yield
    _safe_unlink(backup_path)
