# Публикация изменений WPM: dev -> beta -> main.
# Запуск: двойной клик по publish.bat

$ErrorActionPreference = "Continue"
Set-Location -LiteralPath $PSScriptRoot
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}

function Say($t, $c = "Gray") { Write-Host $t -ForegroundColor $c }
function Step($t) { Write-Host ""; Write-Host "=== $t ===" -ForegroundColor Cyan }
function Fail($t) { Say "ОШИБКА: $t" Red; Read-Host "Нажмите Enter для выхода"; exit 1 }
function RunGit { & git.exe @args; if ($LASTEXITCODE -ne 0) { Fail "команда 'git $args' завершилась с ошибкой" } }
function Ask($q, $default) {
    $a = Read-Host "$q [$default]"
    if ([string]::IsNullOrWhiteSpace($a)) { return $default } else { return $a.Trim() }
}
function Yes($a) { return $a -match '^[yYдД]' }
function Utf8NoBom($path, $text) { [IO.File]::WriteAllText($path, $text, (New-Object Text.UTF8Encoding $false)) }
function AppVersion {
    return (Select-String -Path (Join-Path $PSScriptRoot "core\version.py") -Pattern '^APP_VERSION\s*=\s*"(.+)"').Matches[0].Groups[1].Value
}
function CurrentBranch { return (& git.exe rev-parse --abbrev-ref HEAD).Trim() }
function IsClean { & git.exe diff --quiet; $a = $LASTEXITCODE; & git.exe diff --cached --quiet; return ($a -eq 0 -and $LASTEXITCODE -eq 0) }

function SafetyCheck {
    $staged = & git.exe diff --cached --name-only
    $bad = $staged | Where-Object {
        ($_ -match '\.(pdf|db|log|xlsx|csv)$') -or ($_ -match '(^|/)print_config\.json$') -or
        ($_ -match '(^|/)config_for_laptop\.json$') -or
        ($_ -match '^(dist|build|scratch|history|_backup_[^/]*|\.git_old_backup)/') -or
        (($_ -match '\.exe$') -and ($_ -notmatch '^tools/'))
    }
    if ($bad) {
        $bad | ForEach-Object { Say "  $_" Red }
        & git.exe reset -q
        Fail "в коммит попали личные/лишние файлы (список выше) — добавьте их в .gitignore"
    }
    $big = $staged | Where-Object { (Test-Path -LiteralPath $_) -and ((Get-Item -Force -LiteralPath $_).Length -gt 95MB) }
    if ($big) { & git.exe reset -q; Fail "файлы больше 95 МБ GitHub не примет: $big" }
}

function RunTests {
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) { Say "Python не найден — тесты пропущены (их запустит GitHub)" Yellow; return }
    & python -c "import pytest, pytest_timeout" 2>$null
    if ($LASTEXITCODE -ne 0) { & python -m pip install -q pytest pytest-timeout }
    Say "Тесты (окна программы откроются и закроются сами)..."
    & python -m pytest -q
    if ($LASTEXITCODE -ne 0) {
        if (-not (Yes (Ask "Тесты НЕ прошли. Всё равно отправить? (y/n)" "n"))) { Fail "отправка отменена из-за тестов" }
    } else { Say "Тесты пройдены" Green }
}

function Merge($from, $to) {
    RunGit fetch -q origin
    RunGit switch -q $to
    & git.exe merge -q --ff-only "origin/$to" 2>$null | Out-Null
    & git.exe merge --ff-only $from
    if ($LASTEXITCODE -ne 0) {
        Say "Быстрое слияние невозможно, делаю обычное..." Yellow
        & git.exe merge --no-edit $from
        if ($LASTEXITCODE -ne 0) { & git.exe merge --abort 2>$null; RunGit switch -q dev; Fail "конфликт при слиянии $from -> $to. Нужна ручная помощь." }
    }
    RunGit push origin $to
    RunGit switch -q dev
}

# ───────────────────────── проверки ─────────────────────────
if (-not (Get-Command git.exe -ErrorAction SilentlyContinue)) { Fail "git не установлен" }
if (-not (Test-Path ".git")) { Fail "это не репозиторий git — сначала запустите push_to_github.bat" }

# Файл сборки, если Claude положил новую версию в ci_workflow (в .github ему писать нельзя)
if (Test-Path "ci_workflow\build.yml") {
    New-Item -ItemType Directory -Force ".github\workflows" | Out-Null
    Move-Item -Force "ci_workflow\build.yml" ".github\workflows\build.yml"
    Remove-Item "ci_workflow" -Recurse -Force -ErrorAction SilentlyContinue
    Say "Обновлён .github\workflows\build.yml" Green
}

$branch = CurrentBranch
if ($branch -ne "dev") {
    if (IsClean) { RunGit switch -q dev; Say "Переключился на ветку dev" }
    else { Fail "вы на ветке '$branch' с несохранёнными изменениями. Работать нужно в ветке dev." }
}

$ver = AppVersion
Step "WPM $ver — что делаем?"
Say "  1 — отправить мои изменения в dev   (сборка для канала «Разработка»)"
Say "  2 — выпустить dev  -> beta          (канал «Бета», проверка на работе)"
Say "  3 — выпустить beta -> main          (РАБОЧАЯ версия, канал «Стабильная»)"
$choice = Ask "Номер" "1"

switch ($choice) {
    "1" {
        Step "Отправка в dev"
        & git.exe add -A
        SafetyCheck
        $changed = & git.exe diff --cached --name-only
        if (-not $changed) { Say "Новых изменений нет." Yellow }
        else {
            Say "Изменённые файлы:"; $changed | ForEach-Object { Say "  $_" }
            Say ""
            Say "Опишите изменения одной строкой — это попадёт в список «Что нового» в программе." Yellow
            $msg = Read-Host "Описание"
            if ([string]::IsNullOrWhiteSpace($msg)) { & git.exe reset -q; Fail "описание обязательно" }
            & git.exe reset -q
            RunTests
            & git.exe add -A
            SafetyCheck
            RunGit commit -q -m $msg
        }
        RunGit push origin dev
        Say "Готово: через ~10 минут появится пре-релиз v$ver-dev.N (вкладка Actions/Releases)." Green
    }
    "2" {
        if (-not (IsClean)) { Fail "есть несохранённые изменения — сначала пункт 1" }
        Step "Выпуск dev -> beta"
        RunGit push origin dev
        Merge "dev" "beta"
        Say "Готово: через ~10 минут появится v$ver-beta.N. На рабочем ПК — канал «Бета»." Green
    }
    "3" {
        if (-not (IsClean)) { Fail "есть несохранённые изменения — сначала пункт 1" }
        Step "Выпуск beta -> main (рабочая версия v$ver)"
        & git.exe ls-remote --exit-code --tags origin "refs/tags/v$ver" 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { Fail "релиз v$ver уже есть. Поднимите версию (в dev) и выпустите снова." }
        if (-not (Yes (Ask "Точно выпустить v$ver на все рабочие ПК? (y/n)" "n"))) { Fail "отменено" }
        Merge "beta" "main"
        Say "Готово: через ~10 минут релиз v$ver получат все ПК на канале «Стабильная»." Green

        # Сразу поднимаем версию в dev, чтобы следующий выпуск не упёрся в занятый тег
        $p = $ver.Split("."); $next = "$($p[0]).$($p[1]).$([int]$p[2] + 1)"
        $next = Ask "Новая версия для дальнейшей работы в dev" $next
        $vf = Join-Path $PSScriptRoot "core\version.py"
        $t = [IO.File]::ReadAllText($vf) -replace '(?m)^APP_VERSION\s*=\s*"[^"]*"', "APP_VERSION = `"$next`""
        Utf8NoBom $vf $t
        RunGit add core/version.py
        RunGit commit -q -m "Версия $next (разработка)"
        RunGit push origin dev
        Say "В dev теперь версия $next." Green
    }
    default { Fail "неизвестный пункт: $choice" }
}
Read-Host "Нажмите Enter для выхода"
