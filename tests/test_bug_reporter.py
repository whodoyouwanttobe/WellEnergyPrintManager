"""Отчёты об ошибках: формирование, отправка в GitHub, сохранение офлайн."""
import glob
import io
import json
import os
import urllib.error

import pytest

from core import bug_reporter as br
from core.config import Config


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    cfg = Config()
    cfg.data.update(update_repo="me/wpm", bug_token="tok", tg_token="", tg_chat_id="")
    for f in glob.glob(os.path.join(br.outbox_dir(), "*.json")):
        os.remove(f)
    yield


def test_build_report_contains_context():
    r = br.build_report("Не печатает A1\nподробности", current_file=os.path.join("C:", "проекты", "лист.pdf"),
                        traceback_text="Traceback...\nValueError: x")
    assert "Не печатает A1" in r["title"] and len(r["title"]) <= 120
    assert "Файл: `лист.pdf`" in r["body"]  # только имя файла, без пути
    assert "ValueError" in r["body"]
    assert len(r["body"]) <= br.MAX_BODY


def test_send_github_payload(monkeypatch):
    seen = {}

    def fake_post(url, payload, headers, timeout=15):
        seen.update(url=url, payload=payload, headers=headers)
        return {"number": 7, "html_url": "https://github.com/me/wpm/issues/7"}

    monkeypatch.setattr(br, "_post_json", fake_post)
    status, text = br.deliver(br.build_report("тест"))
    assert status == "github" and "#7" in text
    assert seen["url"] == "https://api.github.com/repos/me/wpm/issues"
    assert seen["headers"]["Authorization"] == "Bearer tok"


def test_labels_rejected_retries_without(monkeypatch):
    calls = []

    def fake_post(url, payload, headers, timeout=15):
        calls.append(dict(payload))
        if "labels" in payload:
            raise urllib.error.HTTPError(url, 422, "Unprocessable", {}, io.BytesIO(b""))
        return {"number": 8}

    monkeypatch.setattr(br, "_post_json", fake_post)
    assert br.send_github(br.build_report("x"))[0] == 8
    assert "labels" not in calls[-1]


def test_offline_saves_and_flushes_later(monkeypatch):
    def offline(*a, **k):
        raise urllib.error.URLError("no network")

    monkeypatch.setattr(br, "_post_json", offline)
    status, _ = br.deliver(br.build_report("офлайн"))
    assert status == "saved"
    files = glob.glob(os.path.join(br.outbox_dir(), "report_*.json"))
    assert len(files) == 1
    assert br.flush_outbox() == 0 and os.path.exists(files[0])  # сеть всё ещё недоступна

    monkeypatch.setattr(br, "_post_json", lambda *a, **k: {"number": 9})
    assert br.flush_outbox() == 1
    assert not os.path.exists(files[0])


def test_no_token_goes_to_outbox():
    Config().data["bug_token"] = ""
    status, text = br.deliver(br.build_report("без токена"))
    assert status == "saved" and "токен" in text
