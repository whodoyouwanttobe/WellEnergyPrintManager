import os
import sys
import tempfile

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Тесты никогда не трогают настоящие настройки и историю печати пользователя:
# APPDATA подменяется ДО первого импорта core.config.
_TMP_APPDATA = tempfile.mkdtemp(prefix="wpm_tests_appdata_")
os.environ["APPDATA"] = _TMP_APPDATA


@pytest.fixture
def sample_folder(tmp_path):
    from ui.selftest import make_sample_pdfs
    make_sample_pdfs(str(tmp_path))
    return str(tmp_path)
