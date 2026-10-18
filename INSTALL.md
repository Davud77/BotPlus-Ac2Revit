# Сборка BotPlus Ac2Revit

Проект рассчитан на Windows, Revit 2026 и Archicad с Tapir 1.7.0. API Revit используется из установленного Revit и не включён в репозиторий. Геометрия готовится локальным CPython 3.12 x64. Текущий C# проект собирается SDK .NET, указанным в `global.json`, и использует целевую платформу `net8.0-windows`.

## Подготовка после клонирования

В PowerShell из корня репозитория:

```powershell
py -3.12 -m venv .ifc-env
.\.ifc-env\Scripts\python.exe -m pip install -r requirements.txt
.\.ifc-env\Scripts\python.exe tools/package.py
```

Вместо последней команды можно использовать:

```powershell
.\tools\package.ps1 -RevitInstallDir 'C:\Program Files\Autodesk\Revit 2026'
```

Четыре необходимых семейства/шаблона находятся в `revit/assets`. Сборка создаёт `dist/Revit2026/Ac2Revit.dll`, `.addin` с абсолютным путём и папку `assets`. Python-подготовщики, `exporter` и `config` должны оставаться рядом с `dist` в корне проекта: размещение одной DLL без остального проекта недостаточно.

## Подключение к Revit

Закройте Revit и скопируйте созданный `.addin`:

```powershell
$addinFolder = Join-Path $env:APPDATA 'Autodesk\Revit\Addins\2026'
New-Item -ItemType Directory -Path $addinFolder -Force | Out-Null
Copy-Item -LiteralPath 'dist/Revit2026/Ac2Revit.addin' -Destination $addinFolder
```

Откройте Archicad с Tapir и затем Revit. Вкладка **BotPlus Ac2Revit** содержит экспорт, продолжение сеанса, сборку и проверку JSON. При загрузке локально собранной неподписанной надстройки Revit может показать свой диалог доверия.

IFC-транслятор выбирается в мастере по списку текущего PLN. Порт Tapir по умолчанию — `19723`. RTE можно выбрать в мастере. Необязательный локальный ADSK-шаблон из `config/exchange-settings.json` в репозиторий не включён; если файла нет, применяется доступный шаблон Revit. Набор доступных аналогов штриховок зависит от выбранного RTE.

## Проверки

```powershell
.\.ifc-env\Scripts\python.exe -m unittest discover -s tests -v
dotnet build revit/Ac2Revit/Ac2Revit.csproj -c Release
```

Проверка конкретного JSON при наличии собственной выгрузки:

```powershell
dotnet run --project tests/ContractCheck -- 'путь\к\manifest.json'
```

## Данные и ограничения

В Git находятся исходники C#/Python, JSON-контракты, конфигурация, тесты, документация и четыре RFA/RFT. Локальные PLN/BPN, IFC/RVT, архивы ответов API, дампы, `artifacts`, `dist`, виртуальные среды и кэши исключены. Исторические отчёты ссылаются на такие локальные файлы; эти ссылки не означают, что файлы опубликованы на GitHub.

Репозиторий — исходный проект, а не готовый универсальный установщик. Параметрическое редактирование доступно только у нативных адаптеров. Точные тела IFC могут сохраняться как DirectShape с указанием ограничений. Помещения принимаются при замкнутой границе и отклонении площади не более 1 м². Растровые текстуры/UV и обновление изменённого исходника поверх существующего импорта пока не реализованы.

Версия v45 собрана и проходит Python-проверки. Пакетное продолжение помещений добавлено после остановки Revit на Ипподроме; полный проход этой новой логики на реальном проекте ещё требует подтверждения.
