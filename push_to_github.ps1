# Загрузка проекта WPM в репозиторий GitHub (ветки main / beta / dev).
# Запуск: двойной клик по push_to_github.bat (или: powershell -ExecutionPolicy Bypass -File push_to_github.ps1)

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
function Utf8NoBom($path, $text) { [IO.File]::WriteAllText($path, $text, (New-Object Text.UTF8Encoding $false)) }

# ───────────────────────── 0. Проверки ─────────────────────────
Step "Проверки"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fail "git не установлен: https://git-scm.com/download/win" }
if (-not (Test-Path "main.py") -or -not (Test-Path "core\version.py")) { Fail "скрипт должен лежать в корне проекта WellEnergyProject" }
foreach ($t in "tools\SumatraPDF.exe", "tools\PDFtoPrinter.exe") {
    if (-not (Test-Path $t)) { Fail "нет $t — без него сборка на GitHub не пройдёт" }
}
$ver = (Select-String -Path "core\version.py" -Pattern '^APP_VERSION\s*=\s*"(.+)"').Matches[0].Groups[1].Value
Say "Версия проекта: $ver" Green

# ───────────────────────── 1. Адрес репозитория ─────────────────────────
Step "Репозиторий"
$oldUrl = ""
if (Test-Path ".git") { $oldUrl = (& git.exe remote get-url origin 2>$null) }
$url = Ask "Адрес репозитория GitHub (https://github.com/владелец/имя.git)" $oldUrl
if ($url -notmatch 'github\.com[:/]([^/]+)/([^/]+?)(\.git)?/?$') { Fail "не похоже на адрес GitHub: $url" }
$owner = $Matches[1]; $repoName = $Matches[2]; $slug = "$owner/$repoName"
Say "Репозиторий: $slug" Green

# Программа будет искать обновления именно в этом репозитории
$upd = "core\updater.py"
$text = [IO.File]::ReadAllText((Join-Path $PSScriptRoot $upd))
$text = [regex]::Replace($text, 'DEFAULT_REPO = "[^"]*"', "DEFAULT_REPO = `"$slug`"")
Utf8NoBom (Join-Path $PSScriptRoot $upd) $text
foreach ($f in "UPDATES.md", "core\config.py") {
    if (Test-Path $f) {
        $t2 = [IO.File]::ReadAllText((Join-Path $PSScriptRoot $f)).Replace("whodoyouwanttobe/WellEnergyPrintManager", $slug)
        Utf8NoBom (Join-Path $PSScriptRoot $f) $t2
    }
}
Say "Адрес обновлений в программе: $slug"

# ───────────────────────── 2. Файл сборки GitHub Actions ─────────────────────────
Step "GitHub Actions"
if (Test-Path "ci_workflow\build.yml") {
    New-Item -ItemType Directory -Force ".github\workflows" | Out-Null
    Move-Item -Force "ci_workflow\build.yml" ".github\workflows\build.yml"
    Remove-Item "ci_workflow" -Recurse -Force -ErrorAction SilentlyContinue
}
if (-not (Test-Path ".github\workflows\build.yml")) { Fail "нет .github\workflows\build.yml" }
Say ".github\workflows\build.yml на месте" Green

# ───────────────────────── 3. История git ─────────────────────────
Step "История git"
$fresh = $true
$hadRepo = Test-Path ".git"
if ($hadRepo) {
    Say "В старой истории (коммит 3.4) есть рендеры чертежей и личный конфиг."
    $ans = Ask "Начать историю заново? Старая история останется только у вас локально (ветка old-history) (y/n)" "y"
    $fresh = $ans -match '^[yYдД]'
}
if (-not $hadRepo) {
    RunGit init -q
} elseif ($fresh) {
    # Папку .git не переименовываем (её может держать открытой IDE/Проводник).
    # Делаем новую ветку без истории; старые коммиты на GitHub не отправляются.
    & git.exe rev-parse --verify -q HEAD 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { RunGit branch -f old-history HEAD }
    $tmpBranch = "wpm-fresh-" + (Get-Date -Format "yyyyMMddHHmmss")
    RunGit checkout -q --orphan $tmpBranch
    RunGit read-tree --empty   # очистить индекс (git rm --cached отказывается, если файл менялся); файлы на диске не трогаются
    Say "Создана новая ветка без старой истории (старая сохранена локально в ветке old-history)" Green
}
& git.exe remote remove origin 2>$null
RunGit remote add origin $url

# Имя/почта для коммитов
if (-not (& git.exe config user.name)) { RunGit config user.name (Ask "Ваше имя для коммитов" $env:USERNAME) }
if (-not (& git.exe config user.email)) { RunGit config user.email (Ask "Ваша почта для коммитов (как на GitHub)" "") }

# ───────────────────────── 4. Тесты (по желанию) ─────────────────────────
Step "Тесты"
$py = Get-Command python -ErrorAction SilentlyContinue
if ($py) {
    & python -c "import pytest, pytest_timeout" 2>$null
    if ($LASTEXITCODE -ne 0) {
        if ((Ask "Установить pytest и pytest-timeout для проверки? (y/n)" "y") -match '^[yYдД]') {
            & python -m pip install -q pytest pytest-timeout
        }
    }
    & python -c "import pytest, pytest_timeout" 2>$null
    if ($LASTEXITCODE -eq 0) {
        Say "Запуск тестов (1-2 минуты, откроются и закроются окна программы)..."
        & python -m pytest -q
        if ($LASTEXITCODE -ne 0) {
            if ((Ask "Тесты НЕ прошли. Всё равно загрузить? (y/n)" "n") -notmatch '^[yYдД]') { Fail "загрузка отменена из-за тестов" }
        } else { Say "Тесты пройдены" Green }
    }
} else { Say "Python не найден — тесты пропущены (их всё равно запустит GitHub)" Yellow }

# ───────────────────────── 5. Коммит ─────────────────────────
Step "Коммит"
# Если история старая — убрать из неё отслеживание личных файлов (на диске они остаются)
foreach ($f in "print_config.json", "config_for_laptop.json", "test_history.db", "print_history.db", "print_manager.py.bak") {
    & git.exe rm --cached -f -q --ignore-unmatch -- $f 2>$null | Out-Null
}
foreach ($f in (& git.exe ls-files -- "*.png" "*.pdf")) {
    if ($f -notlike "ui/assets/*") { & git.exe rm --cached -f -q -- $f 2>$null | Out-Null }
}

& git.exe config core.safecrlf false
RunGit add -A
RunGit add -f "tools/SumatraPDF.exe" "tools/PDFtoPrinter.exe"
if (Test-Path "tools\SumatraPDF-settings.txt") { RunGit add -f "tools/SumatraPDF-settings.txt" }

# Защита: ничего личного и ничего больше 95 МБ (лимит GitHub — 100 МБ)
$staged = & git.exe diff --cached --name-only
$bad = $staged | Where-Object {
    ($_ -match '\.(pdf|db|log|xlsx|csv)$') -or ($_ -match '(^|/)print_config\.json$') -or ($_ -match '(^|/)config_for_laptop\.json$') -or ($_ -match '^(dist|build|scratch|history|_backup_[^/]*|\.git_old_backup)/') -or (($_ -match '\.exe$') -and ($_ -notmatch '^tools/'))
}
if ($bad) { $bad | ForEach-Object { Say "  $_" Red }; Fail "в коммит попали личные/лишние файлы (список выше). Добавьте их в .gitignore" }
$big = $staged | Where-Object { (Test-Path -LiteralPath $_) -and ((Get-Item -Force -LiteralPath $_).Length -gt 95MB) }
if ($big) { $big | ForEach-Object { Say "  $_" Red }; Fail "файлы больше 95 МБ GitHub не примет" }

Say "Файлов в коммите: $($staged.Count)"
& git.exe diff --cached --quiet
if ($LASTEXITCODE -ne 0) { RunGit commit -q -m "WPM $ver`: анализ без зависаний, автообновления, тесты, CI" }
else { Say "Изменений нет — коммит не нужен" }
foreach ($b in "main", "master", "beta", "dev") {
    $cur = (& git.exe rev-parse --abbrev-ref HEAD)
    if ($cur -ne $b) { & git.exe branch -D $b 2>$null | Out-Null }
}
RunGit branch -M main

# ───────────────────────── 6. Настройки GitHub (если есть gh) ─────────────────────────
$gh = Get-Command gh -ErrorAction SilentlyContinue
$ghOk = $false
if ($gh) { & gh auth status 2>$null | Out-Null; $ghOk = ($LASTEXITCODE -eq 0) }
if ($ghOk) {
    Step "Настройки репозитория через gh"
    & gh api -X PUT "repos/$slug/actions/permissions/workflow" -f default_workflow_permissions=write 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { Say "Actions: разрешено создавать релизы" Green }
}

# ───────────────────────── 7. Отправка ─────────────────────────
Step "Отправка на GitHub (может открыться окно входа в GitHub)"
& git.exe push -u origin main
if ($LASTEXITCODE -ne 0) {
    Say "Не удалось отправить main. Обычно это значит, что в репозитории уже есть файлы (README/лицензия)." Yellow
    if ((Ask "Перезаписать содержимое репозитория на GitHub? (y/n)" "n") -match '^[yYдД]') {
        RunGit push -u origin main --force
    } else { Fail "отправка отменена" }
}
RunGit branch -f beta main
RunGit branch -f dev main
RunGit push -u origin beta --force
RunGit push -u origin dev --force
RunGit switch -q dev

if ($ghOk) {
    & gh repo edit $slug --default-branch main 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { Say "Ветка по умолчанию: main" Green }
}

# Старая ветка master на GitHub содержит старую историю — удаляем
& git.exe ls-remote --exit-code --heads origin master 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) {
    & git.exe push origin --delete master 2>$null
    if ($LASTEXITCODE -eq 0) { Say "Старая ветка master на GitHub удалена" Green }
    else { Say "Ветку master на GitHub удалите вручную после смены ветки по умолчанию на main (Branches -> master -> Delete)" Yellow }
}

# ───────────────────────── Итог ─────────────────────────
Step "Готово"
Say "Ветки main, beta и dev загружены в https://github.com/$slug" Green
Say "Сейчас вы на ветке dev — в ней и работайте."
if (-not $ghOk) {
    Say ""
    Say "Осталось сделать вручную на GitHub:" Yellow
    Say "  1. Settings -> Actions -> General -> Workflow permissions -> 'Read and write permissions' -> Save"
    Say "  2. Settings -> General -> Default branch -> main, затем Branches -> удалить master"
    Say "  3. Actions -> 'Test & Release' -> Re-run для ветки main (первый запуск мог упасть на шаге релиза из-за п.1)"
}
Say ""
Say "Через ~10 минут во вкладке Releases появится WPM.exe (релиз v$ver) — его ставьте на рабочий ПК."
Say "Если репозиторий приватный — создайте токен (UPDATES.md, раздел 2) и укажите его в Настройках программы."
Read-Host "Нажмите Enter для выхода"
