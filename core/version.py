# Единственное место, где указывается версия.
# APP_VERSION меняете вы (в ветке dev, когда начинаете новую версию).
# BUILD_CHANNEL и BUILD_NUMBER при сборке в GitHub Actions подставляются автоматически
# (scripts/ci_set_version.py); при локальной сборке остаётся "local".
APP_VERSION = "4.9.1"
BUILD_CHANNEL = "local"   # local / dev / beta / stable
BUILD_NUMBER = 0

if BUILD_CHANNEL in ("dev", "beta"):
    FULL_VERSION = f"{APP_VERSION}-{BUILD_CHANNEL}.{BUILD_NUMBER}"
else:
    FULL_VERSION = APP_VERSION

_suffix = {"dev": " [РАЗРАБОТКА]", "beta": " [БЕТА]", "local": " [локальная сборка]"}.get(BUILD_CHANNEL, "")
APP_TITLE = f"WellEnergy Print Manager {FULL_VERSION}{_suffix}"
