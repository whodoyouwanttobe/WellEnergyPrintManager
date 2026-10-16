"""Автообновление через GitHub Releases.

Схема (подробно — в UPDATES.md):
  ветка dev   -> GitHub Actions собирает exe и публикует пре-релиз  v4.9.0-dev.<N>
  ветка beta  -> пре-релиз                                           v4.9.0-beta.<N>
  ветка main  -> обычный релиз (продакшн)                            v4.9.0

В программе выбирается канал обновлений:
  stable — только релизы из main;
  beta   — бета + stable;
  dev    — всё подряд (для разработчика).

Для приватного репозитория в настройках указывается токен GitHub
(fine-grained, только чтение Contents этого репозитория).
"""
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request

from core.config import Config
from core.version import APP_VERSION as _V, BUILD_CHANNEL, BUILD_NUMBER

DEFAULT_REPO = "whodoyouwanttobe/WellEnergyPrintManager"
APP_VERSION = f"v{_V}"
CHANNEL_RANK = {"dev": 0, "beta": 1, "stable": 2}
ALLOWED = {
    "stable": {"stable"},
    "beta": {"stable", "beta"},
    "dev": {"stable", "beta", "dev"},
}

log = logging.getLogger(__name__)


# ───────────────────────── версии ─────────────────────────
def parse_tag(tag):
    """'v4.9.1-beta.12' -> ((4, 9, 1), 'beta', 12); 'v4.9' -> ((4, 9, 0), 'stable', 0).
    Возвращает None для чужих тегов."""
    m = re.fullmatch(r"v?(\d+)\.(\d+)(?:\.(\d+))?(?:-(beta|dev)\.(\d+))?", str(tag).strip())
    if not m:
        return None
    ver = (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))
    channel = m.group(4) or "stable"
    build = int(m.group(5) or 0)
    return ver, channel, build


def sort_key(parsed):
    """Порядок: 4.9.0-dev.* < 4.9.0-beta.* < 4.9.0 < 4.9.1-dev.* ..."""
    ver, channel, build = parsed
    return ver, CHANNEL_RANK[channel], build


def current_parsed():
    ver = tuple(int(x) for x in (_V.split(".") + ["0", "0"])[:3])
    channel = BUILD_CHANNEL if BUILD_CHANNEL in CHANNEL_RANK else "stable"
    return ver, channel, int(BUILD_NUMBER or 0)


def _parse_version(tag):  # совместимость со старым кодом
    p = parse_tag(tag)
    return p[0] if p else (0,)


def pick_update(releases, channel, current=None):
    """Выбирает самый новый подходящий релиз или None. releases — ответ GitHub API."""
    current = current or current_parsed()
    allowed = ALLOWED.get(channel, ALLOWED["stable"])
    best, best_key = None, sort_key(current)
    for rel in releases:
        if rel.get("draft"):
            continue
        parsed = parse_tag(rel.get("tag_name", ""))
        if not parsed or parsed[1] not in allowed:
            continue
        if sort_key(parsed) > best_key:
            best, best_key = rel, sort_key(parsed)
    return best


# ───────────────────────── сеть ─────────────────────────
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _request(url, token=None, accept="application/vnd.github+json"):
    headers = {"User-Agent": "WPM-Updater", "Accept": accept}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(url, headers=headers)


def _settings():
    cfg = Config()
    repo = (cfg.get("update_repo") or "").strip()
    if "/" not in repo:  # пусто или старое значение "WPM"
        repo = DEFAULT_REPO
    channel = cfg.get("update_channel")
    if channel not in ALLOWED:  # миграция со старой галочки «бета»
        channel = "beta" if cfg.get("beta_channel", False) else "stable"
    token = (cfg.get("update_token") or "").strip() or None
    return repo, channel, token


_ETAG_CACHE = {}   # url -> (etag, данные)


def fetch_releases(repo, token=None, timeout=15):
    """Список релизов. Повторные запросы — «условные» (If-None-Match): если ничего не
    изменилось, GitHub отвечает 304 без тела, и такой ответ не тратит лимит запросов.
    Поэтому программа может проверять обновления часто (раз в несколько минут)."""
    url = f"https://api.github.com/repos/{repo}/releases?per_page=50"
    req = _request(url, token)
    cached = _ETAG_CACHE.get((url, bool(token)))
    if cached:
        req.add_header("If-None-Match", cached[0])
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
            etag = r.headers.get("ETag")
            if etag:
                _ETAG_CACHE[(url, bool(token))] = (etag, data)
            return data
    except urllib.error.HTTPError as e:
        if e.code == 304 and cached:
            return cached[1]
        raise


def _find_assets(release):
    exe = sha = None
    for asset in release.get("assets", []):
        name = asset.get("name", "")
        if name.lower().endswith(".exe") and exe is None:
            exe = asset
        elif name.lower().endswith(".sha256"):
            sha = asset
    return exe, sha


def clean_changelog(body):
    """Убирает служебные строки GitHub, оставляет список изменений."""
    if not body:
        return "Описание изменений не указано."
    lines = [ln for ln in body.replace("\r", "").split("\n")
             if not ln.strip().lower().startswith("**full changelog**")]
    text = "\n".join(lines).strip()
    return (text[:4000] + "\n…") if len(text) > 4000 else (text or "Описание изменений не указано.")


def check_for_updates(callback, manual=False, on_none=None, on_error=None):
    """Проверка в фоне. callback(info) — если есть обновление.
    manual=True — проверять даже если автопроверка выключена."""

    def _check():
        cfg = Config()
        if not manual and not cfg.get("check_updates", True):
            return
        repo, channel, token = _settings()
        if "/" not in repo:
            log.warning(f"Неверный update_repo: {repo!r} (нужно 'владелец/репозиторий')")
            if on_error:
                on_error("В настройках неверно указан репозиторий (нужно владелец/репозиторий).")
            return
        try:
            rel = pick_update(fetch_releases(repo, token), channel)
            if not rel:
                if on_none:
                    on_none()
                return
            exe, sha = _find_assets(rel)
            if not exe:
                log.warning(f"В релизе {rel.get('tag_name')} нет exe")
                if on_none:
                    on_none()
                return
            callback({
                "version": rel.get("tag_name", ""),
                "changelog": clean_changelog(rel.get("body")),
                "asset": exe,
                "sha_asset": sha,
                "channel": parse_tag(rel["tag_name"])[1],
                "repo": repo,
            })
        except urllib.error.HTTPError as e:
            msg = f"GitHub ответил {e.code}"
            if e.code == 404:
                msg += " — репозиторий не найден или приватный (нужен токен в настройках)"
            elif e.code in (401, 403):
                msg += " — проверьте токен или лимит запросов"
            log.error(f"Проверка обновлений: {msg}")
            if on_error:
                on_error(msg)
        except Exception as e:
            log.error(f"Проверка обновлений не удалась: {e}")
            if on_error:
                on_error(str(e))

    threading.Thread(target=_check, daemon=True).start()


def _download_asset(asset, dest, token=None, progress=None):
    """Скачивает файл релиза. Для приватного репо: API-ссылка + токен, затем
    редирект на хранилище GitHub БЕЗ заголовка Authorization (иначе оно отвечает 400)."""
    if token:
        opener = urllib.request.build_opener(_NoRedirect)
        req = _request(asset["url"], token, accept="application/octet-stream")
        try:
            resp = opener.open(req, timeout=30)
        except urllib.error.HTTPError as e:
            if e.code not in (301, 302, 303, 307, 308):
                raise
            resp = urllib.request.urlopen(_request(e.headers["Location"], accept="application/octet-stream"), timeout=30)
    else:
        resp = urllib.request.urlopen(_request(asset["browser_download_url"], accept="application/octet-stream"), timeout=30)

    total = int(resp.headers.get("Content-Length") or asset.get("size") or 0)
    done = 0
    h = hashlib.sha256()
    with resp, open(dest, "wb") as f:
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if progress and total:
                progress(done / total)
    return h.hexdigest()


def download_update(info, progress=None):
    """Скачивает и проверяет обновление. Возвращает путь к новому exe. Вызывать в фоне."""
    _, _, token = _settings()
    temp_dir = tempfile.mkdtemp(prefix="wpm_update_")
    new_exe = os.path.join(temp_dir, "WPM_update.exe")
    digest = _download_asset(info["asset"], new_exe, token, progress)

    if info.get("sha_asset"):
        sha_file = os.path.join(temp_dir, "sum.sha256")
        _download_asset(info["sha_asset"], sha_file, token)
        with open(sha_file, encoding="utf-8", errors="ignore") as f:
            expected = f.read().split()[0].strip().lower()
        if digest.lower() != expected:
            os.remove(new_exe)
            raise RuntimeError("Контрольная сумма не совпала — обновление повреждено, установка отменена.")
    else:
        log.warning("В релизе нет .sha256 — целостность не проверена")

    if os.path.getsize(new_exe) < 5 * 1024 * 1024:
        raise RuntimeError("Скачанный файл слишком маленький — похоже, загрузка оборвалась.")
    return new_exe


_PYI_ENV_PREFIXES = ("_PYI_", "_MEIPASS")
_PYI_ENV_NAMES = {"TCL_LIBRARY", "TK_LIBRARY", "TKPATH", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"}


def clean_child_env(environ=None):
    """Окружение для запуска НОВОГО независимого экземпляра программы."""
    env = dict(os.environ if environ is None else environ)
    meipass = getattr(sys, "_MEIPASS", None)
    for key in list(env):
        if key.upper().startswith(_PYI_ENV_PREFIXES) or key.upper() in _PYI_ENV_NAMES:
            env.pop(key)
        elif meipass and isinstance(env[key], str) and meipass in env[key] and key.upper() != "PATH":
            env.pop(key)   # любые другие переменные, указывающие во временную папку старого exe
    if meipass and "PATH" in env:
        sep = ";" if (sys.platform == "win32" or ";" in env["PATH"]) else os.pathsep
        env["PATH"] = sep.join(p for p in env["PATH"].split(sep) if meipass not in p)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"   # официальный флаг PyInstaller 6.9+
    return env


def install_update(new_exe, restart=True):
    """Заменяет текущий exe новым (после закрытия программы) и, если restart, запускает его.
    Старый exe сохраняется как *.old."""
    current_exe = sys.executable
    if not getattr(sys, "frozen", False):
        raise RuntimeError(f"Программа запущена из исходников. Новый exe скачан сюда:\n{new_exe}")

    bat_path = os.path.join(os.path.dirname(new_exe), "update.bat")
    # Ждём, пока текущий exe освободится (до 60 с), делаем резервную копию, подменяем.
    script = f"""@echo off
chcp 65001 >nul
set "NEW={new_exe}"
set "CUR={current_exe}"
set N=0
:wait
timeout /t 1 /nobreak >nul
copy /Y "%CUR%" "%CUR%.old" >nul 2>&1
move /Y "%NEW%" "%CUR%" >nul 2>&1
if not errorlevel 1 goto ok
set /a N+=1
if %N% LSS 60 goto wait
:ok
{'start "" "%CUR%"' if restart else 'rem без перезапуска'}
del "%~f0"
"""
    with open(bat_path, "w", encoding="utf-8") as f:
        f.write(script)
    # ВАЖНО: чистое окружение. Иначе перезапущенный exe наследует переменные PyInstaller
    # (_PYI_*, _MEIPASS2, TCL_LIBRARY…) и думает, что он «дочерний процесс» старого exe:
    # ищет python311.dll во временной папке старого процесса, которая уже удалена →
    # «Failed to load Python DLL … _MEIxxxx\python311.dll».
    subprocess.Popen(["cmd", "/c", bat_path], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                     close_fds=True, env=clean_child_env())


# Совместимость со старым вызовом
def apply_update(download_url, sha256_url=None):
    raise RuntimeError("apply_update устарел: используйте download_update + install_update")
