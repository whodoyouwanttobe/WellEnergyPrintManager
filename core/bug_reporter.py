"""Отчёты об ошибках -> GitHub Issues вашего репозитория.

Почему GitHub, а не Telegram: api.telegram.org из российских сетей часто недоступен,
а GitHub открывается без VPN (через него же идут обновления программы).

Порядок отправки:
  1. GitHub Issues (токен «bug_token» с правом Issues: Read and write);
  2. Telegram — только если настроен (запасной канал, может не работать без VPN);
  3. если ничего не получилось — отчёт сохраняется в %APPDATA%\\WellEnergyPrintManager\\reports
     и отправляется автоматически при следующем запуске программы.
"""
import glob
import json
import logging
import os
import platform
import time
import urllib.error
import urllib.parse
import urllib.request

from core.config import Config
from core.version import FULL_VERSION, BUILD_CHANNEL

log = logging.getLogger(__name__)

LOG_TAIL_LINES = 120
MAX_BODY = 60000          # лимит GitHub на тело issue — 65536 символов
DEFAULT_LABELS = ["bug", "из-программы"]


def _app_dir():
    appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
    d = os.path.join(appdata, "WellEnergyPrintManager")
    os.makedirs(d, exist_ok=True)
    return d


def outbox_dir():
    d = os.path.join(_app_dir(), "reports")
    os.makedirs(d, exist_ok=True)
    return d


def _log_tail(lines=LOG_TAIL_LINES):
    logs = glob.glob(os.path.join(_app_dir(), "*.log"))
    if not logs:
        return ""
    path = max(logs, key=os.path.getmtime)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-lines:])
    except OSError:
        return ""


def build_report(description, current_file=None, traceback_text=None, kind="user"):
    """Собирает отчёт (словарь). Вызывается в момент нажатия кнопки, чтобы лог был актуальным."""
    first_line = description.strip().splitlines()[0] if description.strip() else "Без описания"
    prefix = "Сбой" if kind == "crash" else "Ошибка"
    title = f"[{FULL_VERSION}] {prefix}: {first_line}"[:120]

    parts = [
        "### Описание",
        description.strip() or "_нет_",
        "",
        "### Окружение",
        f"- Версия: `{FULL_VERSION}` (канал `{BUILD_CHANNEL}`)",
        f"- Компьютер: `{os.environ.get('COMPUTERNAME', platform.node())}`",
        f"- Windows: `{platform.platform()}`",
        f"- Время: `{time.strftime('%Y-%m-%d %H:%M:%S')}`",
        f"- Файл: `{os.path.basename(current_file)}`" if current_file else "- Файл: не выбран",
    ]
    if traceback_text:
        parts += ["", "### Трассировка", "```", traceback_text.strip()[-8000:], "```"]
    tail = _log_tail()
    if tail:
        parts += ["", f"<details><summary>Лог (последние {LOG_TAIL_LINES} строк)</summary>", "", "```",
                  tail[-(MAX_BODY - 12000):], "```", "</details>"]
    body = "\n".join(parts)[:MAX_BODY]
    return {"title": title, "body": body, "created": time.time(), "kind": kind}


# ───────────────────────── каналы отправки ─────────────────────────
def _repo_and_token():
    from core.updater import DEFAULT_REPO
    cfg = Config()
    repo = (cfg.get("update_repo") or "").strip()
    if "/" not in repo:
        repo = DEFAULT_REPO
    token = (cfg.get("bug_token") or "").strip()
    return repo, token


def _post_json(url, payload, headers, timeout=15):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={**headers, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def send_github(report):
    """Создаёт issue. Возвращает (номер, ссылка). Исключение — если не получилось."""
    repo, token = _repo_and_token()
    if not token:
        raise RuntimeError("не указан токен для отчётов (Настройки → Система)")
    url = f"https://api.github.com/repos/{repo}/issues"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "User-Agent": "WPM-BugReporter", "X-GitHub-Api-Version": "2022-11-28"}
    payload = {"title": report["title"], "body": report["body"], "labels": DEFAULT_LABELS}
    try:
        res = _post_json(url, payload, headers)
    except urllib.error.HTTPError as e:
        if e.code == 422:  # метки недоступны токену — отправляем без них
            payload.pop("labels")
            res = _post_json(url, payload, headers)
        elif e.code in (401, 403):
            raise RuntimeError(f"GitHub отклонил токен ({e.code}): нужен доступ Issues: Read and write к {repo}")
        elif e.code == 404:
            raise RuntimeError(f"репозиторий {repo} не найден или токен к нему не имеет доступа")
        else:
            raise
    return res.get("number"), res.get("html_url")


def send_telegram(report):
    cfg = Config()
    token, chat = (cfg.get("tg_token") or "").strip(), (cfg.get("tg_chat_id") or "").strip()
    if not token or not chat:
        raise RuntimeError("Telegram не настроен")
    text = f"{report['title']}\n\n{report['body']}"
    if len(text) > 4000:
        text = text[:1500] + "\n...\n" + text[-2400:]
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode("utf-8")
    with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=10) as r:
        res = json.loads(r.read().decode("utf-8"))
    if not res.get("ok"):
        raise RuntimeError(res.get("description", "Telegram вернул ошибку"))


def deliver(report):
    """Пробует все каналы. Возвращает (статус, текст): 'github' | 'telegram' | 'saved'."""
    errors = []
    try:
        number, link = send_github(report)
        return "github", f"Отчёт #{number} создан на GitHub.\n{link or ''}".strip()
    except Exception as e:
        errors.append(f"GitHub: {e}")
        log.warning(f"Отчёт в GitHub не отправлен: {e}")
    cfg = Config()
    if cfg.get("tg_token") and cfg.get("tg_chat_id"):
        try:
            send_telegram(report)
            return "telegram", "Отчёт отправлен в Telegram."
        except Exception as e:
            errors.append(f"Telegram: {e}")
            log.warning(f"Отчёт в Telegram не отправлен: {e}")
    path = save_to_outbox(report)
    return "saved", ("Сейчас отправить не удалось — отчёт сохранён и уйдёт автоматически "
                     "при следующем запуске программы.\n\n" + "\n".join(errors) + f"\n\nФайл: {path}")


# ───────────────────────── отложенная отправка ─────────────────────────
def save_to_outbox(report):
    path = os.path.join(outbox_dir(), f"report_{int(report['created'] * 1000)}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False)
    return path


def flush_outbox(max_age_days=30):
    """Отправляет сохранённые отчёты. Вызывается в фоне при запуске. Возвращает число отправленных."""
    sent = 0
    for path in sorted(glob.glob(os.path.join(outbox_dir(), "report_*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                report = json.load(f)
            if time.time() - report.get("created", 0) > max_age_days * 86400:
                os.remove(path)
                continue
            report["body"] = "_Отчёт был сохранён офлайн и отправлен позже._\n\n" + report["body"]
            send_github(report)
            os.remove(path)
            sent += 1
        except Exception as e:
            log.info(f"Отложенный отчёт {os.path.basename(path)} пока не отправлен: {e}")
            break  # нет сети/токена — остальные тоже не уйдут, попробуем в следующий раз
    if sent:
        log.info(f"Отправлено отложенных отчётов: {sent}")
    return sent
