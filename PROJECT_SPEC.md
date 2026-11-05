# СПЕЦИФИКАЦИЯ ПРОЕКТА: SIMS 4 DLC ARCHIVE & INSTALL MANAGER (WHITE LABEL PLATFORM)

- **Версия спецификации:** 1.1.5
- **Дата фиксации:** 2026-11-04
- **Статус:** Утверждено к локальной реализации

---

## 1. ЦЕЛЬ ПРОЕКТА

Создание модульного Windows-приложения с графическим интерфейсом на CustomTkinter для автоматического поиска расположения игры, загрузки, криптографической проверки и прямой файловой установки архивов дополнений (DLC) The Sims 4 с публичных ресурсов Яндекс.Диска непосредственно в каталог установленной игры без использования и без взаимодействия с механизмами управления контентом лаунчеров Steam, Origin или EA App.

Приложение включает в себя:
1. Автономную систему сканирования и анализа целостности дополнений The Sims 4 на уровне файловой системы с обязательным аппаратным контролем файловой системы NTFS через `GetVolumeInformationW`.
2. Потоковый загрузчик архивов с поддержкой HTTP Range (код 206), возобновлением и контролем хеш-сумм (SHA-256 / MD5).
3. Инспектор и безопасный распаковщик архивов ZIP, 7Z, RAR с защитой от Path Traversal и интеллектуальной нормализацией вложенности папок.
4. Систему установки на базе детерминированного конечного автомата (FSM) с раздельными базами данных SQLite WAL: критический журнал состояний с `PRAGMA synchronous=FULL` в `state.db`, строгим разделением статусов FSM и жизненного цикла lease, а также протоколом обработки неопределённых коммитов с проверкой целостности схемы и таблиц.
5. Двухфазный файловый установщик с перемещением оригиналов в резервную область тома и использованием `ReplaceIfExists = FALSE` для исключения перезаписи сторонних файлов.
6. Процедуру идемпотентного аварийного восстановления (Crash Recovery) с обязательным удержанием открытых дескрипторов под `dwShareMode = FILE_SHARE_READ` на всё время инспекции, предварительной проверкой аномалий Hard Link для всех пар путей (Target-Backup, Target-Staging, Backup-Staging), разграничением кодов инспекции (`OBJECT_NOT_FOUND`, `ACCESS_DENIED`, `SHARING_VIOLATION`, `OBJECT_IDENTITY_MISMATCH`, `HASH_MISMATCH`, `IO_ERROR`, `VERIFIED_OBJECT`), тройной верификацией (`VolumeSerialNumber`, `FileId`, `SHA-256`) и строгим правилом `UNMATCHED_TUPLE` (Fail-Closed).
7. Модуль контролируемой установки и запуска вспомогательной доверенной утилиты из архива «dlc помощник.zip» с установкой рабочей директории в корень игры строго после явного подтверждения пользователя (User Consent Gate).
8. Режимы предпросмотра изменений (Dry Run), изолированной проверки архива (Verify Only), систему резервного копирования и отката (Rollback).
9. Платформу White Label для генерации независимых брендированных сборок под 2–5 магазинов из единой кодовой базы.

---

## 2. ИСХОДНЫЕ ДАННЫЕ И ИСТОЧНИКИ

### 2.1. Основные публичные источники по умолчанию
- **Основной каталог архивов DLC:**
  - URL: `https://disk.yandex.ru/d/pYDq0CSIwqclFA`
  - Название папки: `The Sims 4 DLC PLAYEROK FANFIRIEL`
- **Доверенный вспомогательный инструмент:**
  - URL: `https://disk.yandex.ru/d/RgVixKr-6QlLsQ`
  - Название архива: `dlc помощник.zip`

### 2.2. Содержимое облачного хранилища
- Каталоги дополнений: `EP01`..`EPxx`, `GP01`..`GPxx`, `SP01`..`SPxx`, `FP01`, комплекты (Kits).
- Служебные каталоги и архивы: `__Installer`, `Delta`, `Data`, `Sound` и др.
- Пакеты данных: файлы `.package`, языковые компоненты (например, `Strings_RUS_RU.package`).
- Вспомогательные бинарные и скриптовые компоненты в составе доверенного архива помощника.

---

## 3. СИСТЕМНЫЕ ТРЕБОВАНИЯ И ТЕХНОЛОГИЧЕСКИЙ СТЕК

- **Целевая платформа:** Windows 10 x64, Windows 11 x64 (standalone x64 без необходимости установки Python в системе пользователя).
- **Базовый интерпретатор:** Python 3.12 x64.
- **Интерфейс пользователя:** CustomTkinter (тёмная тема, адаптивные компоненты, динамическая палитра тем).
- **Сетевое взаимодействие:** Requests, urllib3 (сессии, потоковая загрузка чанками, поддержка Range/Content-Range, экспоненциальные повторы при кодах 429/5xx).
- **База данных:** SQLite 3 (две раздельные базы данных в режиме `PRAGMA journal_mode=WAL`: `state.db` с `synchronous=FULL` для журнала транзакций и `catalog.db` с `synchronous=NORMAL` для кэша облака).
- **Криптография и безопасность:**
  - `cryptography.hazmat.primitives.asymmetric.ed25519` (цифровые подписи релизов и токенов).
  - Windows DPAPI (`CryptProtectData`/`CryptUnprotectData` через `ctypes.windll.crypt32`) для локального шифрования секретов владельца.
  - PBKDF2-HMAC-SHA256 + AES-256-GCM для переносимого резервного хранилища ключей (Key Vault).
  - Модуль `hashlib` (SHA-256, MD5).
- **Архиваторы:** Встроенный `zipfile`, библиотеки `py7zr`, `rarfile` (с регистрацией `unrar.dll` в runtime).
- **Низкоуровневые API Windows (`ctypes.windll.ntdll`, `ctypes.windll.kernel32`, `ctypes.windll.shell32`, `winreg`):**
  - Нативный NT API: `NtCreateFile`, `NtClose` для относительного открытия дескрипторов каталогов и файлов с правами `FILE_ADD_FILE` (`0x0002`) и `DELETE` (`0x00010000`) без следования по Reparse Points.
  - Дескрипторные Win32 API: `CreateFileW`, `GetFinalPathNameByHandleW`, `GetFileInformationByHandleEx` (`FileIdInfo`), `SetFileInformationByHandle` (`FileRenameInfo`, `FileDispositionInfo`), `CloseHandle`, `FlushFileBuffers`.
  - Проверка файловой системы: `GetVolumeInformationW` (строгая верификация файловой системы `NTFS`).
  - Межпроцессная координация: файловые блокировки `tx_<tx_id>.lock` (`dwShareMode = 0`), токены возобновления в файлах со строгой проверкой статуса FSM `SUSPENDED_UAC_REQUIRED` и активного `LockContext`.
  - Определение томов: `GetVolumePathNameW`.
  - Запуск с повышением прав: `ShellExecuteExW` с глаголом `runas`.
  - Чтение реестра Windows для поиска установленной игры.
- **Сборка дистрибутивов:** PyInstaller (манифест `asInvoker`, запрет консольного окна, оптимизация зависимостей).

---

## 4. ДЕТАЛЬНАЯ АРХИТЕКТУРА СИСТЕМЫ

### 4.1. Разделение сред (Customer Mode vs Owner Mode)
Проект строится на строгой физической и логической изоляции:
1. **Клиентский дистрибутив (Customer Mode):**
   - Standalone EXE конкретного магазина.
   - Включает модули ядра `core/`, базу данных `database/`, брендинг `branding/`, клиентский GUI `gui/`.
   - Включает открытый ключ `ed25519_public.pem`, манифест `release-manifest.json` и цифровую подпись `release-manifest.sig`.
   - Не содержит административного кода, утилит сборщика, закрытых ключей или личных OAuth-токенов.
   - Запрещает ручное редактирование URL источников, названий и брендинга.
2. **Инструментарий владельца (Owner Tools):**
   - Набор локальных CLI/GUI скриптов (`owner_tools/`), запускаемых исключительно из исходного кода на рабочей станции владельца.
   - Управляет генерацией ключей, тестированием хранилища, валидацией источников, проверкой профилей и компиляцией сборок через PyInstaller.

### 4.2. Структура репозитория и файлов проекта
```
Sims4DLCArchiveManager/
│
├── PROJECT_SPEC.md                      # Полная спецификация проекта
├── PROJECT_PROGRESS.md                  # Журнал этапов разработки и верификации
├── requirements.txt                     # Зависимости клиентского приложения
├── requirements-dev.txt                 # Зависимости окружения владельца (pyinstaller, cryptography)
├── main.py                              # Точка входа клиентского приложения
│
├── owner_tools/                         # ИСКЛЮЧИТЕЛЬНО ДЛЯ ВЛАДЕЛЬЦА (В СБОРКУ НЕ ВХОДИТ)
│   ├── __init__.py
│   ├── build_manager.py                 # Движок валидации и сборки релизов
│   ├── builder_cli.py                   # Консольный интерфейс сборщика
│   ├── owner_gui.py                     # Локальная панель владельца
│   ├── config_validator.py              # Валидация профилей, проверка доступности API
│   ├── release_manager.py               # Формирование release-manifest.json и SHA-256
│   ├── signing.py                       # Подпись манифестов закрытым ключом Ed25519
│   ├── key_vault.py                     # Управление переносимым бэкапом ключа и challenge
│   └── token_manager.py                 # Управление личным OAuth (Windows DPAPI владельца)
│
├── configs/                             # Профили магазинов White Label
│   └── stores/
│       ├── store_alpha/
│       │   ├── store.json               # Конфигурация магазина
│       │   ├── theme.json               # Тема оформления
│       │   ├── release-manifest.json    # Манифест целостности ресурсов магазина
│       │   ├── release-manifest.sig     # Подпись манифеста Ed25519
│       │   └── assets/                  # Логотипы, иконки, баннеры
│       └── store_beta/
│           ├── store.json
│           ├── theme.json
│           ├── release-manifest.json
│           ├── release-manifest.sig
│           └── assets/
│
├── core/                                # Программное ядро приложения
│   ├── __init__.py
│   ├── exceptions.py                    # Иерархия типизированных исключений
│   ├── security.py                      # Верификация Ed25519, DPAPI, lease-токены и LockContext [Этап 1]
│   ├── nt_handle.py                     # Обертка над ntdll.NtCreateFile (RootDirectory + No Reparse) [Этап 7]
│   ├── app_context.py                   # Контекст магазина: store_id, изолированные пути
│   ├── game_finder.py                   # Поиск игры (реестр, Steam, EA App) + проверка NTFS [Этап 2]
│   ├── yandex_client.py                 # REST API Яндекс.Диска (каталог, пагинация, ссылки)
│   ├── downloader.py                    # Резюмируемый загрузчик (Range 206, .part)
│   ├── archive_manager.py               # Единый интерфейс к ZIP, 7Z, RAR
│   ├── archive_inspector.py             # Анализ структуры DLC, нормализация путей, белый список
│   ├── installer.py                     # Двухфазный Handle-based установщик (CREATE и OVERWRITE без Replace)
│   ├── post_install_helper.py           # Распаковщик и лаунчер помощника (cwd=game_root, consent)
│   ├── integrity.py                     # Хеширование, Crash Recovery верификатор под LockContext
│   ├── backup_manager.py                # Резервирование файлов игры и Saves/Tray/Mods
│   └── logger.py                        # Ротируемый логгер с маскированием чувствительных данных
│
├── database/                            # Локальное хранилище данных SQLite
│   ├── __init__.py
│   ├── database.py                      # Инициализация соединений: state.db (FULL) и catalog.db (NORMAL)
│   ├── models.py                        # Схемы таблиц: state.db (tx, file_log, lease), catalog.db (items)
│   └── history.py                       # Интерфейс доступа к журналу транзакций
│
├── branding/                            # Модули презентационного слоя
│   ├── __init__.py
│   ├── branding_manager.py              # Инициализация профиля магазина при старте
│   ├── theme_manager.py                 # Управление стилями и темами CustomTkinter
│   └── asset_manager.py                 # Загрузка и валидация графических ресурсов
│
├── gui/                                 # Графический интерфейс пользователя
│   ├── __init__.py
│   ├── app.py                           # Корневое окно приложения (Single Window)
│   ├── components/                      # Виджеты: заголовок, прогресс-карты, тосты
│   └── views/                           # Представления: Dashboard, Catalog, Downloads, Backups, History, Settings
│
├── tests/                               # Автоматические тесты
│   ├── __init__.py
│   ├── conftest.py                      # Песочницы, моки путей Windows, фикстуры
│   ├── test_security_crypto.py
│   ├── test_context.py                  # Тест изоляции хранилищ по store_id
│   ├── test_key_vault_automated.py
│   ├── test_import_isolation.py
│   ├── test_game_finder.py              # Проверка поиска игры и детекции файловой системы NTFS
│   ├── test_yandex_client.py
│   ├── test_downloader.py
│   ├── test_archive_engine.py
│   ├── test_database_wal.py             # Проверка PRAGMA synchronous=FULL и протокола COMMIT
│   ├── test_installer_crash_recovery.py # Полная детерминированная матрица Crash Recovery и FileId
│   ├── test_uac_flow.py                 # Тест lease-токена, LockContext и UAC-перезапуска
│   ├── test_path_security_handles.py    # Тест NtCreateFile с RootDirectory и прав FILE_ADD_FILE
│   ├── test_post_install_helper.py      # Тест интеграции вспомогательного модуля
│   ├── test_branding_themes.py
│   ├── test_complex_integration.py
│   └── test_build_artifacts.py
│
└── releases/                            # Артефакты сборки
    ├── store_alpha/
    │   ├── Sims4_Store_Alpha.exe
    │   ├── SHA256.txt
    │   └── release-manifest.json
    └── store_beta/
        ├── Sims4_Store_Beta.exe
        ├── SHA256.txt
        └── release-manifest.json
```

---

## 5. WHITE LABEL АРХИТЕКТУРА И ИЗОЛЯЦИЯ ДАННЫХ

### 5.1. Принципы White Label
- Единое общее ядро (`core/`) обслуживает все варианты магазинов.
- Каждый магазин обладает уникальным неизменяемым идентификатором `store_id` (например, `store_alpha`, `store_beta`).
- Для каждого магазина изолированно настраиваются:
  - Название приложения (`application_name`) и магазина (`store_name`);
  - Графические ассеты (логотип, иконка окна, баннер главного экрана, иконка бинарника EXE);
  - Цветовая палитра темы CustomTkinter (`The Sims 4 Style`, `Modern Gaming`, `Minimal Pro`, `Marketplace`, `Custom Theme`);
  - Конфигурация URL облачных источников DLC и вспомогательного архива;
  - Матрица доступных пользователю функциональных возможностей (`enabled_features`).

### 5.2. Изоляция пользовательских данных в Windows
Клиентские приложения разных магазинов физически разделяют хранилища в каталоге `%LOCALAPPDATA%`:
```
%LOCALAPPDATA%/Sims4DLCArchiveManager/
│
├── store_alpha/
│   ├── database/
│   │   ├── state.db              (Журнал транзакций, FSM, synchronous=FULL)
│   │   └── catalog.db            (Кэш каталога и загрузок, synchronous=NORMAL)
│   ├── downloads/                (Архивы и .part файлы)
│   ├── user_backups/             (Резервные копии Saves/Tray/Mods)
│   ├── locks/                    (Файлы взаимной блокировки транзакций)
│   ├── logs/app.log              (Ротируемый журнал)
│   └── cache/                    (Временный кэш)
│
└── store_beta/
    ├── database/
    │   ├── state.db
    │   └── catalog.db
    ├── downloads/
    ├── user_backups/
    ├── locks/
    ├── logs/app.log
    └── cache/
```
Смешивание баз данных, настроек, кэша и загрузок между магазинами категорически запрещено.

### 5.3. Правило локализации Staging, транзакционных резервных копий и контроль NTFS
1. **Строгая зависимость от файловой системы NTFS:**
   - Для папки игры и рабочего staging-каталога допускается использование **строго файловой системы NTFS**.
   - При определении тома вызовом `GetVolumePathNameW` программа выполняет системный вызов `GetVolumeInformationW`. Если выходной параметр `lpFileSystemNameBuffer` возвращает значение, отличное от `L"NTFS"` (например, `FAT32`, `exFAT`, `ReFS` или сетевые файловые системы), приложение **немедленно блокирует операцию до создания транзакции в БД и до любых модификаций диска игры**, возбуждая исключение `UnsupportedFilesystemError`.
   - *Разделение понятий:*
     - *Свойства NTFS:* сохранение 128-битных `FileId` внутри тома при переименованиях; ведение журнала транзакций файловой системы `$LogFile` для обеспечения целостности метаданных при сбоях.
     - *Свойства Win32/NT API:* семантика `SetFileInformationByHandle` с классом `FileRenameInfo` и флагом `ReplaceIfExists = FALSE`, возвращающая отказ `STATUS_OBJECT_NAME_COLLISION` при занятости имени.
     - *Предположения и ограничения архитектуры:* приложение предполагает, что игра установлена на локальном томе с монопольным доступом пользователя; на томах с сетевой переадресацией или сжатием поведение дескрипторов требует отдельной валидации.
2. **Локализация рабочей области:**
   Временная директория для загрузки, распаковки и инспекции файлов DLC (`staging`), а также **каталог транзакционных резервных копий (`tx_backups`)** создаются **строго на том же логическом дисковом томе NTFS**, где расположена целевая папка игры:
   - Пример: если игра установлена в `D:\Games\The Sims 4`, то рабочая директория размещается по пути вида:
     `D:\.s4_staging_{store_id}_{hash}\`
     Внутри неё создаются подкаталоги:
     - `D:\.s4_staging_{store_id}_{hash}\incoming\` (распакованные файлы из облака)
     - `D:\.s4_staging_{store_id}_{hash}\tx_backups\` (резервные копии оригинальных заменяемых файлов)
3. **Модель согласованности установки (отсутствие сквозной атомарности):**
   - Размещение staging и транзакционных бэкапов на том же томе даёт возможность совершать быстрые пофайловые перемещения внутри MFT через `SetFileInformationByHandle` без длительного межтомного копирования.
   - **SQLite WAL обеспечивает транзакционность исключительно записей внутри базы данных.** Файловая система Windows и SQLite не объединены в распределённую двухфазную транзакцию. Сквозной атомарности всей многофайловой установки на уровне операционной системы не существует.
   - В случае внезапного отключения питания файловая система неизбежно останется в промежуточной конфигурации: часть файлов уже будет заменена в каталоге игры, часть останется в staging, а часть — в резервной копии.
   - Согласованность данных достигается разделением ролей:
     - **`state.db` (SQLite WAL, `synchronous=FULL`):** надёжный сброс намерений операций, хешей, путей, `staging_file_id` и `original_file_id` до выполнения Win32/NT API.
     - **`catalog.db` (SQLite WAL, `synchronous=NORMAL`):** производительное хранение кэша облачных ссылок и списков DLC, восстановимое при сбое.
     - **FSM:** управление жизненным циклом транзакции от `CREATED` до терминального статуса.
     - **Файловый движок:** двухфазное перемещение (оригинал $\rightarrow$ `tx_backups`, staging $\rightarrow$ целевой путь) строго под контролем `LockContext.guard()` с `ReplaceIfExists = FALSE`.
     - **Crash Recovery Engine:** процедура при старте приложения под контролем `LockContext`, которая сравнивает фактические идентификаторы MFT и хеши на диске с журналом и приводит файловую систему к согласованному состоянию.

---

## 6. КРИПТОГРАФИЧЕСКАЯ БЕЗОПАСНОСТЬ И RELEASE GUARD

### 6.1. Модель ключей Ed25519
- **Мастер-ключ владельца (`ed25519_master.key`):**
  Хранится строго вне репозитория, вне каталогов сборки и вне клиентских сборок — в системном защищённом каталоге профиля владельца:
  `%APPDATA%\Sims4DLCArchiveManager_OwnerKeyring\ed25519_master.key`
  Локально защищён с помощью Windows DPAPI (`CryptProtectData`).
- **Открытый ключ (`ed25519_public.pem`):**
  Вкомпилируется в клиентский EXE для проверки подписей манифестов и токенов.

### 6.2. Переносимое резервное хранилище (Key Vault)
- Экспорт закрытого ключа в зашифрованный файл `.vault` выполняется с использованием алгоритма **AES-256-GCM**.
- Ключ шифрования генерируется на основе мастер-пароля владельца через **PBKDF2-HMAC-SHA256** (600 000 итераций) со случайной криптографической солью (32 байта) и случайным IV (12 байт).
- Формат контейнера: `[MAGIC (4B) | SALT (32B) | IV (12B) | GCM_TAG (16B) | CIPHERTEXT]`.

### 6.3. Двухуровневое тестирование резервной копии ключа
Выпуск релизов блокируется до выполнения двух независимых проверок:
1. **Автоматический тест (`test_key_vault_automated.py`):**
   - Моделирует недоступность DPAPI (мок функции `CryptUnprotectData` с ошибкой Access Denied).
   - Выполняет дешифрование тестового vault по паролю в оперативной памяти, подписывает тестовое сообщение и проверяет открытым ключом.
2. **Интеграционный тест на чистой Windows-среде (Clean VM):**
   - Проводится на отдельной изолированной машине без доступа к DPAPI владельца.
   - Скрипт запрашивает пароль у владельца через защищённый консольный ввод (пароль не логируется и не сохраняется).
   - Формирует подписанный токен `integration_test_token.sig`.

### 6.4. Канонический JSON-токен интеграционного теста
Структура полезной нагрузки токена:
```json
{
  "version": "1.0",
  "test_id": "int_test_20261104_01",
  "challenge": "<challenge_hex>",
  "public_key_fp": "SHA256:...",
  "vault_fp": "SHA256:...",
  "timestamp": "2026-11-04T12:00:00Z",
  "recovery_success": true
}
```
**Правила валидации и канонизации:**
- Запрещены дублирующиеся ключи JSON (парсер использует `object_pairs_hook` с проверкой коллизий).
- До проверки подписи проводится обязательная строгая валидация типов данных и допустимых значений полей.
- Каноническая байтовая форма формируется строго детерминированно:
  `json.dumps(payload, separators=(',', ':'), sort_keys=True, ensure_ascii=False).encode('utf-8')`.

### 6.5. Механизм Release Guard (Аппаратный предохранитель сборки)
Release Guard в модуле `owner_tools/build_manager.py` блокирует компиляцию при любых расхождениях.
**Транзакционная последовательность Release Guard:**
1. Динамический запуск `test_key_vault_automated.py` — требуется нулевой код возврата процесса.
2. Проверка Ed25519 подписи токена `integration_test_token.sig` открытым ключом.
3. Сверка `public_key_fp` и `vault_fp` из токена с фактическими хешами текущих локальных файлов.
4. Открытие эксклюзивной транзакции в `challenge_registry.db`:
   ```sql
   UPDATE challenges 
   SET status = 'CONSUMED' 
   WHERE challenge = ? 
     AND test_id = ? 
     AND expected_public_key_fp = ? 
     AND expected_vault_fp = ? 
     AND status = 'ISSUED';
   ```
5. Проверка `rowcount == 1`. Если challenge отсутствует или уже погашен другим процессом сборки — немедленное прерывание с `ROLLBACK`.
6. Сборка запускается только после успешного `COMMIT`.
7. Если компиляция PyInstaller упала после погашения challenge — повторное использование токена запрещено; владелец обязан пройти процедуру генерации challenge и подписи токена заново.

---

## 7. WINDOWS PATH SECURITY, ДЕСКРИПТОРНЫЙ ДВИЖОК, UAC И СТРОГИЙ КОНТРАКТ ВЛАДЕНИЯ LOCK

### 7.1. Реальные ограничения Win32/NT API и остаточные риски
1. **Исключение строкового разрешения пути:** Все манипуляции с дочерними объектами выполняются строго через `ntdll.NtCreateFile` с указанием дескриптора проверенного родительского каталога в поле `OBJECT_ATTRIBUTES.RootDirectory`. Использование строковых путей Win32 `CreateFileW` в критическом конвейере установки исключено.
2. **Исключение слепого уничтожения файлов через `ReplaceIfExists = FALSE`:** Стандартное перемещение с `ReplaceIfExists = TRUE` не позволяет проверить, какой именно объект замещается в момент вызова, создавая критический TOCTOU-риск перезаписи чужого файла. Использование `ReplaceIfExists = FALSE` как для эвакуации оригинала в бэкап, так и для установки staging-файла гарантирует, что если целевое имя занято сторонним объектом, системный вызов вернёт отказ `STATUS_OBJECT_NAME_COLLISION`, предотвратив перезапись.
3. **Разделение гарантий долговечности (NTFS vs Файловые буферы vs Аппаратный кеш):**
   - Вызов `FlushFileBuffers(hBackup)` сбрасывает на накопитель данные конкретного файла, но **не гарантирует атомарной фиксации метаданных каталога NTFS** (записи `$INDEX_ROOT` / `$INDEX_ALLOCATION` родительской папки могут оставаться в кеше драйвера файловой системы).
   - Потребительские SSD без Power Loss Protection (PLP) могут терять незафиксированные блоки метаданных из энергозависимого кеша контроллера при внезапном обесточивании.
   - Программа может строго проверить целостность данных на момент вызова (FileId, Volume, SHA-256 после открытия), но долговечность после обесточивания ограничена свойствами накопителя и журнала NTFS.
4. **Безопасное поведение:** Если дескриптор не может быть открыт, если обнаружена неразрешённая точка повторной обработки или идентификаторы тома не совпадают — файловая операция **немедленно блокируется со статусом `CONFLICT`**.

### 7.2. Карта прав доступа Windows API и контракт `ntdll.NtCreateFile`
Для исключения повторного строкового разрешения путей спуск по дереву папок выполняется пошагово от доверенного дескриптора `game_root` с использованием нативного NT API:

#### 7.2.1. Исчерпывающая таблица прав дескрипторов Windows API (12 операций)

| Операция | Целевой объект | Используемый Handle | Требуемый `DesiredAccess` | Числовая маска | `ShareAccess` (`dwShareMode`) | Права родителя источника | Права родителя назначения | Применяемый API | Техническое обоснование прав |
|---|---|---|---|---|---|---|---|---|---|
| **1. Навигация по каталогам** | Промежуточный каталог | `hIntermediateDir` | `FILE_LIST_DIRECTORY \| FILE_TRAVERSE \| FILE_READ_ATTRIBUTES \| SYNCHRONIZE` | `0x001000A1` | `FILE_SHARE_READ \| FILE_SHARE_WRITE` (`0x03`) | — | — | `ntdll.NtCreateFile` | `FILE_TRAVERSE` (`0x0020`) необходим для прохода вглубь дерева, `FILE_READ_ATTRIBUTES` (`0x0080`) для проверки FileIdInfo и Reparse Points. |
| **2. Открытие целевого родителя** | Каталог назначения (игра) | `hDestParentDir` | `FILE_ADD_FILE \| FILE_LIST_DIRECTORY \| FILE_READ_ATTRIBUTES \| SYNCHRONIZE` | `0x00100083` | `FILE_SHARE_READ \| FILE_SHARE_WRITE` (`0x03`) | — | — | `ntdll.NtCreateFile` | При перемещении файла в каталог ядро NTFS добавляет запись в индекс родителя, что требует обязательного права `FILE_ADD_FILE` (`0x0002`) на `RootDirectory`. |
| **3. Открытие родителя источника** | Каталог staging или оригинал | `hSourceParentDir` | `FILE_LIST_DIRECTORY \| FILE_READ_ATTRIBUTES \| SYNCHRONIZE` | `0x00100081` | `FILE_SHARE_READ \| FILE_SHARE_WRITE` (`0x03`) | — | — | `ntdll.NtCreateFile` | Проверка отсутствия точек монтирования и принадлежности тому до начала перемещения. |
| **4. Открытие исходного файла** | Файл staging или оригинал | `hSourceFile` | `DELETE \| FILE_READ_DATA \| FILE_READ_ATTRIBUTES \| SYNCHRONIZE` | `0x00110081` | `FILE_SHARE_READ` (`0x01`) | — | — | `ntdll.NtCreateFile` (с `RootDirectory = hParentDir`) | `FILE_READ_DATA` (`0x0001`) нужен для снятия SHA-256. Право `DELETE` (`0x00010000`) на файле позволяет удалить его имя из старого каталога без явного права `FILE_DELETE_CHILD` на родительский каталог (стандарт NTFS). Запрет `FILE_SHARE_WRITE` блокирует модификацию файла сторонними процессами во время хеширования. |
| **5. Перемещение staging в цель** | Файл staging $\rightarrow$ игра | `hStagingFile` (передаётся дескриптор `hDestParentDir` в `RootDirectory`) | Вызывается на `hStagingFile` | `0x00110081` | `FILE_SHARE_READ` (`0x01`) | `0x00100081` | `0x00100083` | `SetFileInformationByHandle(FileRenameInfo)` | `FILE_RENAME_INFO` выполняет внутритомовую смену родителя. `ReplaceIfExists = FALSE` гарантирует отказ `STATUS_OBJECT_NAME_COLLISION` при занятости имени. |
| **6. Эвакуация оригинала в бэкап** | Оригинал игры $\rightarrow$ `tx_backups` | `hOriginalFile` (передаётся дескриптор `hBackupDir` в `RootDirectory`) | Вызывается на `hOriginalFile` | `0x00110081` | `FILE_SHARE_READ` (`0x01`) | `0x00100081` | `0x00100083` на `tx_backups` | `SetFileInformationByHandle(FileRenameInfo)` | Перемещение исходного файла игры в резервный каталог без его уничтожения. `ReplaceIfExists = FALSE`. |
| **7. Возврат backup в игру** | Файл бэкапа $\rightarrow$ игра | `hBackupFile` (передаётся дескриптор `hDestParentDir` в `RootDirectory`) | Вызывается на `hBackupFile` | `0x00110081` | `FILE_SHARE_READ` (`0x01`) | `0x00100081` на `tx_backups` | `0x00100083` на игре | `SetFileInformationByHandle(FileRenameInfo)` | Возврат эвакуированного оригинального файла на штатное место в игре. `ReplaceIfExists = FALSE`. |
| **8. Удаление установленного файла** | Целевой установленный файл | `hTargetFile` | `DELETE \| SYNCHRONIZE` | `0x00110000` | `FILE_SHARE_READ \| FILE_SHARE_WRITE \| FILE_SHARE_DELETE` (`0x07`) | — | — | `SetFileInformationByHandle(FileDispositionInfo)` + `CloseHandle` | Право `DELETE` на файл переводит флаг `DeleteFile = TRUE`. Файл физически удаляется ядром NTFS при закрытии дескриптора. На родителе `FILE_DELETE_CHILD` не требуется при наличии `DELETE` на файле. |
| **9. Очистка staging-файла** | Неиспользованный staging | `hStagingFile` | `DELETE \| SYNCHRONIZE` | `0x00110000` | `FILE_SHARE_READ \| FILE_SHARE_WRITE \| FILE_SHARE_DELETE` (`0x07`) | — | — | `SetFileInformationByHandle(FileDispositionInfo)` + `CloseHandle` | Физическое удаление проверенного staging-файла после отката или коммита. |
| **10. Очистка файла бэкапа** | Отработанный бэкап | `hBackupFile` | `DELETE \| SYNCHRONIZE` | `0x00110000` | `FILE_SHARE_READ \| FILE_SHARE_WRITE \| FILE_SHARE_DELETE` (`0x07`) | — | — | `SetFileInformationByHandle(FileDispositionInfo)` + `CloseHandle` | Удаление бэкап-файла после фиксации терминального статуса транзакции `COMMITTED`. |
| **11. Создание подкаталогов** | Папки `staging`, `tx_backups` | Дескриптор родительского каталога | `FILE_ADD_SUBDIRECTORY \| FILE_ADD_FILE \| FILE_LIST_DIRECTORY \| FILE_TRAVERSE \| FILE_READ_ATTRIBUTES \| SYNCHRONIZE` | `0x001000A7` | `FILE_SHARE_READ \| FILE_SHARE_WRITE` (`0x03`) | — | — | `ntdll.NtCreateFile` (с `CreateDisposition = FILE_CREATE=0x02` и `CreateOptions = FILE_DIRECTORY_FILE=0x01`) | Создание каталогов выполняется строго через `NtCreateFile` относительно родительского дескриптора. `FILE_ADD_SUBDIRECTORY` (`0x0004`) требуется для создания дочерней MFT-записи каталога. |
| **12. Удаление пустых каталогов** | Каталоги транзакции после очистки | Дескриптор удаляемого каталога | `DELETE \| FILE_LIST_DIRECTORY \| SYNCHRONIZE` | `0x00110001` | `FILE_SHARE_READ \| FILE_SHARE_WRITE \| FILE_SHARE_DELETE` (`0x07`) | — | — | `SetFileInformationByHandle(FileDispositionInfo)` + `CloseHandle` | Удаление пустых рабочих каталогов при завершении транзакции. |

#### 7.2.2. Контракт системного вызова `NtCreateFile`
```python
# Структуры x64:
# OBJECT_ATTRIBUTES: 48 байт (Length=48, RootDirectory, ObjectName, Attributes=OBJ_CASE_INSENSITIVE=0x40)
# UNICODE_STRING: 16 байт (Length, MaximumLength, Buffer)
# IO_STATUS_BLOCK: 16 байт (Status, Information)
```
**Параметры `NtCreateFile`:**
- `DesiredAccess`: `0x00100083` (целевой каталог назначения), `0x001000A1` (навигационный сегмент) или `0x00110081` (открытие исходного файла).
- `ShareAccess`: `FILE_SHARE_READ | FILE_SHARE_WRITE` (`0x0001 | 0x0002`, без `FILE_SHARE_DELETE`) для каталогов; `FILE_SHARE_READ` (`0x0001`) для исходных файлов.
- `CreateDisposition`: `FILE_OPEN` (`0x00000001` — открытие существующего) или `FILE_CREATE` (`0x00000002` — создание нового каталога).
- `CreateOptions`: `FILE_DIRECTORY_FILE | FILE_SYNCHRONOUS_IO_NONALERT | FILE_OPEN_FOR_BACKUP_INTENT | FILE_OPEN_REPARSE_POINT` (`0x00000001 | 0x00000020 | 0x00004000 | 0x00200000`) для каталогов; `FILE_NON_DIRECTORY_FILE | FILE_SYNCHRONOUS_IO_NONALERT | FILE_OPEN_FOR_BACKUP_INTENT` для файлов.

#### 7.2.3. Правила валидации и закрытия
1. **Обработка NTSTATUS:** `STATUS_SUCCESS` (`0x00000000`) — успех. При `STATUS_OBJECT_NAME_NOT_FOUND` (`0xC0000034`) фиксируется `OBJECT_NOT_FOUND`. При `STATUS_ACCESS_DENIED` (`0xC0000022`) — фиксируется `ACCESS_DENIED` и анализируется по разделу 7.5.
2. **Проверка Reparse Points:** Вызывается Win32 `GetFileInformationByHandle`. Если установлен `FILE_ATTRIBUTE_REPARSE_POINT`, точка повторной обработки отсечена флагом `FILE_OPEN_REPARSE_POINT`. В структуре дополнений Reparse Points запрещены регламентом: дескриптор немедленно закрывается, фиксируется `CONFLICT`.
3. **Проверка тома:** Вызывается `GetFileInformationByHandleEx(FileIdInfo)`: сверяется `VolumeSerialNumber` с томом игры. Несовпадение блокирует операцию.
4. **Закрытие дескрипторов:** Промежуточные дескрипторы закрываются через `ntdll.NtClose` в блоке `finally` сразу после успешного открытия следующего дочернего дескриптора. Дескриптор конечного родительского каталога удерживается открытым.

---

### 7.3. Раздельные алгоритмы `CREATE` и `OVERWRITE` (Двухфазная эвакуация)

#### 7.3.1. Алгоритм `CREATE` (Атомарный отказ при занятом имени)
1. В `staging` подготавливается файл под защитой `LockContext.guard()`, дескриптор `hStaging` открывается с `dwShareMode = FILE_SHARE_READ` (`0x01`), вызывается `FlushFileBuffers(hStaging)`.
2. Запрашивается `GetFileInformationByHandleEx(FileIdInfo)` для staging-файла: фиксируются `volume_serial` и 128-битный `staging_file_id`.
3. В `state.db` (с `PRAGMA synchronous=FULL`) записывается намерение:
   `action_type = CREATE`, `original_sha256 = NULL`, `target_sha256`, `staging_file_id`, статус `PENDING`. Выполняется `COMMIT`.
4. Под защитой `LockContext.guard()` вызывается `SetFileInformationByHandle` со структурой `FILE_RENAME_INFO`:
   - `ReplaceIfExists = FALSE` (`0x00`).
   - `RootDirectory` = дескриптор проверенного родительского каталога игры (открытый с правом `0x00100083`).
   - `FileName` = имя целевого файла.
5. **Атомарный отказ:** Если целевое имя уже занято (файлом, созданным сторонним процессом до или во время операции), ядро NTFS возвращает ошибку `STATUS_OBJECT_NAME_COLLISION` (`ERROR_ALREADY_EXISTS`).
   - Сторонний файл **не перезаписывается**.
   - Операция останавливается со статусом `CONFLICT`.
6. При успехе: запрашивается `FileIdInfo` целевого файла. Так как перемещение происходит внутри одного тома NTFS, `target_file_id` строго равен `staging_file_id`. Дескриптор `hStaging` закрывается. В `state.db` фиксируется статус `APPLIED` и `installed_file_id`.

#### 7.3.2. Алгоритм `OVERWRITE` (Полный жизненный цикл дескрипторов и двухфазная эвакуация)
Для исключения уничтожения сторонних файлов перезапись выполняется строго в два шага с `ReplaceIfExists = FALSE` под защитой `LockContext.guard()` с непрерывным удержанием блокировок совместного доступа:

1. **Шаг 1: Открытие `hOriginal` и хеширование:**
   - Дескриптор `hOriginal` открывается через `ntdll.NtCreateFile` с правами `0x00110081` (`DELETE | FILE_READ_DATA | FILE_READ_ATTRIBUTES | SYNCHRONIZE`) и монопольным чтением `dwShareMode = FILE_SHARE_READ` (`0x00000001`). Запрет совместной записи гарантирует неизменность файла во время снятия хеша.
   - **Поведение при наличии сторонних дескрипторов:** Если другой процесс уже открыл файл на запись или без права совместного чтения, вызов немедленно завершается ошибкой `ERROR_SHARING_VIOLATION`. Операция прерывается со статусом `CONFLICT` без ожидания и без модификаций.
   - Запрашивается `FileIdInfo`: фиксируются `volume_serial` и 128-битный `original_file_id`.
   - Непосредственно через открытый `hOriginal` считывается содержимое и вычисляется `original_sha256`.
2. **Шаг 2: Фиксация намерения эвакуации в `state.db`:**
   - Запись намерения: `action_type = OVERWRITE`, `original_file_id`, `original_sha256`, `staging_file_id`, `target_sha256`, статус `PENDING`.
   - Выполняется `COMMIT` с `PRAGMA synchronous=FULL`. Намерение зафиксировано до модификации файловой системы.
3. **Шаг 3: Перемещение оригинала в `tx_backups`:**
   - Исходный файл перемещается в резервную папку тома (`tx_backups`) через `SetFileInformationByHandle(FileRenameInfo)`:
     - `ReplaceIfExists = FALSE` (`0x00`).
     - `RootDirectory` = дескриптор каталога `tx_backups` на том же томе (открытый с правом `0x00100083`).
     - `FileName` = уникальное имя бэкапа (включает `tx_id` и `original_file_id`).
   - Закрывается дескриптор `hOriginal`. Целевое имя в каталоге игры физически освобождено.
4. **Шаг 4: Открытие и захват дескриптора `hBackup`:**
   - Немедленно открывается дескриптор созданного бэкапа `hBackup` в папке `tx_backups` с правами `0x00110081` и `dwShareMode = FILE_SHARE_READ`.
5. **Шаг 5: Проверка целостности и сброс буферов:**
   - Запрашивается `FileIdInfo`: проверяется равенство тома и `backup.FileId == original_file_id`.
   - Считывается содержимое и вычисляется хеш: проверяется равенство `backup_sha256 == original_sha256`.
   - Вызывается `FlushFileBuffers(hBackup)`.
   - **ТРЕБОВАНИЕ БЕЗОПАСНОСТИ:** Дескриптор `hBackup` **НЕ ЗАКРЫВАЕТСЯ**. Он остаётся непрерывно открытым с `dwShareMode = FILE_SHARE_READ`, блокируя любую стороннюю модификацию или удаление бэкапа на протяжении всего Шага 7 и Шага 8 вплоть до успешного коммита `APPLIED` в `state.db`.
6. **Шаг 6: Фиксация статуса backup в журнале:**
   - В `state.db` фиксируется `backup_status = 'VERIFIED'`, `backup_file_id = original_file_id`.
   - Выполняется `COMMIT` (`synchronous=FULL`).
7. **Шаг 7: Установка staging-файла в освободившееся имя:**
   - Staging-файл удерживается открытым через `hStaging` с `dwShareMode = FILE_SHARE_READ` и перемещается в целевое имя:
     - `ReplaceIfExists = FALSE` (`0x00`).
     - `RootDirectory` = дескриптор родительского каталога игры (с правом `0x00100083`).
     - `FileName` = имя целевого файла.
   - **Защита от сторонней подмены:** Если сторонний процесс занял освободившееся имя, вызов завершается ошибкой `STATUS_OBJECT_NAME_COLLISION` (`ERROR_ALREADY_EXISTS`). Сторонний файл не перезаписывается, оригинал остаётся под защитой удерживаемого открытым дескриптора `hBackup`, фиксируется `CONFLICT`.
8. **Шаг 8: Завершение и освобождение дескрипторов:**
   - Запрашивается `FileIdInfo` установленного файла, подтверждается `installed_file_id == staging_file_id`.
   - В `state.db` фиксируется статус `APPLIED`, выполняется `COMMIT` (`synchronous=FULL`).
   - Только после подтверждения коммита закрывается удерживаемый дескриптор `hBackup`.

---

### 7.4. Протокол изоляции транзакции, FSM и безопасный UAC-перезапуск

Владение Win32 Mutex привязано к создавшему его потоку ОС и не может быть передано другому процессу. Передача секретов через аргументы командной строки запрещена.

**Ограничение аутентификации между процессами одного пользователя:**
Файл `%LOCALAPPDATA%/.../locks/uac_resume_<tx_id>.dat` защищается стандартным списком контроля доступа (DACL), разрешающим доступ только текущему пользователю Windows и встроенной группе Администраторов (`BA`). Однако любой процесс, запущенный в той же пользовательской сессии Windows с обычными правами, имеет те же учетные данные и технически способен прочитать этот файл. Поэтому `resume_secret` **не является криптографическим средством аутентификации против локального вредоносного ПО, запущенного от того же пользователя**. Он служит строго механизмом одноразовой координации и защиты от случайных параллельных запусков.

**Строгое разделение FSM и статусов Lease:**
- Статусы транзакции (FSM): `CREATED`, `PREPARED`, `BACKUP_CREATED`, `INSTALLING`, `SUSPENDED_UAC_REQUIRED`, `ABORTED_ELEVATION_REJECTED`, `INSTALLED`, `COMMITTED`, `ROLLING_BACK`, `ROLLED_BACK`, `CRASH_RECOVERY`, `ABORTED_RECOVERED`, `RECOVERY_CONFLICT`.
- Статусы lease (`transaction_leases`): `LEASE_ACTIVE`, `LEASE_SUSPENDED_UAC`, `LEASE_EXPIRED_UAC_REJECTED`, `LEASE_TERMINATED`.
- Статус `ACTIVE` относится **исключительно** к статусу аренды `transaction_leases` (`LEASE_ACTIVE`) и не используется как статус транзакции FSM. Транзакция после успешного возобновления переходит строго в статус FSM `INSTALLING` (или `ROLLING_BACK` при откате).

**Схема таблицы `transaction_leases` и условные SQL-переходы:**
```sql
CREATE TABLE transaction_leases (
    tx_id TEXT PRIMARY KEY,
    lease_token TEXT NOT NULL,
    owner_pid INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('LEASE_ACTIVE', 'LEASE_SUSPENDED_UAC', 'LEASE_EXPIRED_UAC_REJECTED', 'LEASE_TERMINATED')),
    handoff_hash TEXT,
    acquired_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    FOREIGN KEY (tx_id) REFERENCES transactions(tx_id)
);
```
Наличие ограничения `tx_id PRIMARY KEY` гарантирует на уровне СУБД, что для одной транзакции может существовать **ровно одна запись аренды**.

**Строгая последовательность UAC-перехода:**
1. **Остановка файловых операций:** При получении `ERROR_ACCESS_DENIED` и подтверждении необходимости elevation (по разделу 7.5) все файловые действия немедленно останавливаются.
2. **Проверка текущего lease:** Проверяется валидность активного контекста `LockContext`: дескриптор `tx_<tx_id>.lock` удерживается открытым, в таблице `transaction_leases` статус равен `LEASE_ACTIVE`. При потере дескриптора или расхождении токена конвейер немедленно прерывается со статусом `CONFLICT`.
3. **Генерация секрета возобновления:** PID 1 генерирует криптографический 32-байтный `resume_secret`.
4. **Запись файла секрета:** Секрет записывается в файл `%LOCALAPPDATA%/.../locks/uac_resume_<tx_id>.dat` с DACL, ограничивающим доступ текущим пользователем и администраторами.
5. **Атомарная фиксация состояния в `state.db`:**
   - Выполняются условные обновления с обязательной проверкой `rowcount == 1`:
     ```sql
     UPDATE transactions SET status = 'SUSPENDED_UAC_REQUIRED' WHERE tx_id = ? AND status = 'INSTALLING';
     UPDATE transaction_leases SET status = 'LEASE_SUSPENDED_UAC', handoff_hash = ?, updated_at = CURRENT_TIMESTAMP WHERE tx_id = ? AND lease_token = ? AND status = 'LEASE_ACTIVE';
     ```
   - Выполняется `COMMIT` (`PRAGMA synchronous=FULL`). Если `rowcount != 1` или коммит завершился ошибкой — файловые операции запрещены, процесс аварийно останавливается.
6. **Вызов `ShellExecuteExW` с непрерывным удержанием блокировки:**
   - `ShellExecuteExW` с глаголом `runas` ожидает ответа пользователя на системный диалог UAC синхронно.
   - **PID 1 не закрывает `state.db` и не освобождает дескриптор `tx_<tx_id>.lock` до возврата из `ShellExecuteExW`!**
   - **Ветвь отказа пользователя (`ERROR_CANCELLED`):**
     - Если пользователь нажимает «Нет» в окне UAC, вызов `ShellExecuteExW` возвращает `FALSE` (`ERROR_CANCELLED`).
     - Поскольку PID 1 **всё ещё удерживает дескриптор `tx_<tx_id>.lock` и открытое соединение с `state.db`**, сторонний процесс не может захватить транзакцию.
     - PID 1 выполняет единый `COMMIT` (`synchronous=FULL`):
       ```sql
       UPDATE transactions SET status = 'ABORTED_ELEVATION_REJECTED' WHERE tx_id = ? AND status = 'SUSPENDED_UAC_REQUIRED';
       UPDATE transaction_leases SET status = 'LEASE_EXPIRED_UAC_REJECTED', lease_token = 'EXPIRED', handoff_hash = NULL, updated_at = CURRENT_TIMESTAMP WHERE tx_id = ? AND status = 'LEASE_SUSPENDED_UAC';
       ```
     - Проверяется `rowcount == 1`. PID 1 удаляет файл `uac_resume_<tx_id>.dat`.
     - Частично перенесённые файлы консервируются в каталоге игры (так как PID 1 не имеет прав на их откат в `Program Files`). Пользователю выводится уведомление о необходимости ручного запуска от Администратора для отката.
     - PID 1 закрывает дескрипторы и штатно завершает работу.
   - **Ветвь согласия пользователя:**
     - Пользователь нажимает «Да». ОС Windows создаёт привилегированный процесс PID 2, вызов `ShellExecuteExW` возвращает `TRUE`.
     - Только после успешного возврата PID 1 закрывает дескриптор `tx_<tx_id>.lock`, закрывает соединение с `state.db` и немедленно завершает работу (`sys.exit(0)`).
7. **Вход повышенного процесса (PID 2):**
   - PID 2 запускается с правами Администратора.
   - PID 2 открывает файл блокировки `tx_<tx_id>.lock` с `dwShareMode = 0` (выполняя до 50 попыток с задержкой 100 мс для ожидания завершения PID 1).
   - Если за 5 секунд дескриптор не освобождён (или перехвачен третьим процессом PID 3) — PID 2 **не совершает никаких файловых операций** и завершает работу с кодом конфликта.
   - PID 2 открывает `state.db`. Проверяет статус транзакции: **возобновление разрешено строго в статусе FSM `SUSPENDED_UAC_REQUIRED` и статусе lease `LEASE_SUSPENDED_UAC`**. Если статус иной — выход.
   - PID 2 открывает `uac_resume_<tx_id>.dat`, считывает `resume_secret` и сверяет его хэш с сохранённым в `state.db`. При несовпадении — выход.
8. **Атомарная инвалидация секрета и фиксация нового lease:**
   - PID 2 генерирует новый `lease_token = UUIDv4`.
   - PID 2 выполняет единый `COMMIT` (`synchronous=FULL`):
     ```sql
     UPDATE transactions SET status = 'INSTALLING' WHERE tx_id = ? AND status = 'SUSPENDED_UAC_REQUIRED';
     UPDATE transaction_leases SET lease_token = ?, owner_pid = ?, status = 'LEASE_ACTIVE', handoff_hash = NULL, updated_at = CURRENT_TIMESTAMP WHERE tx_id = ? AND status = 'LEASE_SUSPENDED_UAC';
     ```
   - Проверяется `rowcount == 1`. Только после подтверждения коммита файл `uac_resume_<tx_id>.dat` удаляется с диска.
   - PID 2 формирует валидный `LockContext` и продолжает установку в статусе FSM `INSTALLING`.

---

### 7.5. Дифференциация причин ERROR_ACCESS_DENIED и защита от циклов UAC
Ошибка `ERROR_ACCESS_DENIED` (`0x00000005`) не является автоматическим триггером повышения прав. Программа строго дифференцирует причины отказа:

1. **Обоснованные условия для запроса UAC:**
   - Ошибка `ERROR_ACCESS_DENIED` возникла при вызове `NtCreateFile` в каталоге игры.
   - Проверка родительского каталога игры показывает наличие ограничивающего ACL (каталог расположен в `%ProgramFiles%`, `%ProgramFiles(x86)%` или системном корне `%SystemDrive%\Games`).
   - Процесс запущен с уровнем целостности Medium (стандартный пользователь) без привилегий Администратора.
   - **Счётчик попыток:** Транзакция допускает **строго одну** попытку elevation-перезапуска (`elevation_attempts == 0`).
2. **Случаи, когда UAC категорически запрещён (остановка с диагностикой):**
   - **Атрибут Read-Only:** На целевом файле установлен атрибут `FILE_ATTRIBUTE_READONLY` (вызывает `ERROR_ACCESS_DENIED` при открытии на запись или удаление независимо от прав администратора). Устраняется снятием атрибута, а не elevation.
   - **Ошибка в staging-директории:** `ACCESS_DENIED` возник в `%LOCALAPPDATA%` или `staging` (указывает на повреждение прав пользовательского профиля или блокировку антивирусом).
   - **Reparse Point / Политика безопасности:** Ошибка вызвана блокировкой Software Restriction Policies / AppLocker или попыткой открытия запрещённой точки повторной обработки.
   - **Повторный отказ:** Процесс уже запущен от имени Администратора (High Integrity Level), но всё равно получил `ERROR_ACCESS_DENIED` (файл заблокирован фильтр-драйвером антивируса или повреждён дескриптор безопасности).
   - В этих случаях диалог UAC **не предлагается**. Конвейер останавливается, пользователю выводится диагностическое сообщение о блокировке конкретного файла антивирусом или атрибутом Read-Only, статус переводится в `CONFLICT`.

---

### 7.6. Обязательный контракт владения Lock/Lease при файловых операциях

1. **Разделение взаимного исключения процессов и авторизации:**
   - **Взаимное исключение процессов:** Гарантируется открытым дескриптором файла `%LOCALAPPDATA%/.../locks/tx_<tx_id>.lock`, открытым через `CreateFileW` с флагами `GENERIC_READ | GENERIC_WRITE`, `dwShareMode = 0` (запрет любого совместного доступа).
   - **Авторизация транзакции (Lease):** Запись в `state.db` в таблице `transaction_leases` хранит `(tx_id, lease_token, status)`. Проверка `lease_token` в базе данных **не заменяет живой файловый дескриптор** и не обеспечивает взаимной блокировки процессов сама по себе.
2. **Структура контекста блокировки (`LockContext`):**
   Все изменяющие файловые функции установщика (`installer.py`), эвакуации (`backup_manager.py`) и восстановления (`integrity.py`) обязаны принимать проверенный экземпляр структуры:
   ```python
   class LockContext:
       tx_id: str
       lease_token: str
       lock_handle: int              # Win32 HANDLE на tx_<tx_id>.lock
       expected_lock_file_id: int    # 128-битный FileId lock-файла
       expected_lock_volume_serial: int
       lock_sync: threading.RLock    # Внутрипроцессная синхронизация потоков
       is_active: bool
   ```
3. **Строгий протокол удержания `lock_sync` во время Win32/NT вызовов:**
   Любая изменяющая файловая операция (переименование, эвакуация, удаление, очистка staging, возврат backup, Crash Recovery) обязана выполняться строго внутри контекстного менеджера `with lock_context.guard():` по следующему контракту:
   - Шаг 1: Поток захватывает внутрипроцессную блокировку `lock_sync.acquire()`.
   - Шаг 2: Проверяется флаг `is_active == True`.
   - Шаг 3: Проверяется действительность дескриптора вызовом Win32 `GetHandleInformation(lock_handle, &flags)`.
   - Шаг 4: Вызывается `GetFileInformationByHandleEx(lock_handle, FileIdInfo)`: подтверждается равенство `VolumeSerialNumber == expected_lock_volume_serial` и `FileId == expected_lock_file_id`.
   - Шаг 5: Проверяется соответствие `tx_id` и `lease_token` активной транзакции в `state.db` со статусом `LEASE_ACTIVE`.
   - Шаг 6: **Дескрипторная блокировка `lock_sync` остаётся непрерывно захваченной на всём протяжении вызова низкоуровневого изменяющего Win32/NT API** (`NtCreateFile`, `SetFileInformationByHandle`, `FlushFileBuffers` или `CloseHandle` удаляемого дескриптора).
   - Шаг 7: После завершения системного вызова и фиксации кода возврата блокировка `lock_sync` освобождается.
4. **Защита от закрытия дескриптора из параллельных потоков:**
   Любое закрытие дескриптора `lock_handle` (при отмене, завершении или ошибке) обязано выполняться **строго под защитой того же `lock_sync`**: поток закрытия захватывает `lock_sync`, выставляет `is_active = False`, закрывает дескриптор `CloseHandle(lock_handle)`, обнуляет `lock_handle = 0` и освобождает `lock_sync`. Это аппаратно исключает ситуацию, когда один поток закрывает дескриптор в момент выполнения файловой операции другим потоком.
5. **Правило Crash Recovery:**
   Процедура Crash Recovery является модифицирующей операцией. Она **не имеет права выполнять файловые изменения без предварительного захвата `tx_<tx_id>.lock` с `dwShareMode = 0`** и формирования валидного `LockContext`. При получении `ERROR_SHARING_VIOLATION` Crash Recovery останавливается без изменения файлов игры.

---

### 7.7. Сценарий последующего восстановления после отказа UAC (ABORTED_ELEVATION_REJECTED)

Если пользователь отказался от UAC (`ERROR_CANCELLED`), транзакция остаётся в статусе FSM `ABORTED_ELEVATION_REJECTED`, а lease переведён в `LEASE_EXPIRED_UAC_REJECTED`. Последующее восстановление выполняется по отдельному независимому регламенту:

1. **Самостоятельный запуск с правами Администратора:** Пользователь позднее вручную запускает приложение через «Запуск от имени администратора».
2. **Обнаружение незавершённой транзакции:** При инициализации приложение опрашивает `state.db` и обнаруживает транзакцию в статусе `ABORTED_ELEVATION_REJECTED`.
3. **Захват файлового lock:** Приложение вызывает `CreateFileW` на `tx_<tx_id>.lock` с `dwShareMode = 0`. При неудаче (`ERROR_SHARING_VIOLATION`) — выход без действий.
4. **Проверка аннулирования старого lease и регистрация нового:**
   - Приложение проверяет таблицу `transaction_leases`: убеждается, что прежний lease находится строго в статусе `LEASE_EXPIRED_UAC_REJECTED`.
   - Генерируется новый `lease_token = UUIDv4`.
   - Выполняются условные обновления с проверкой `rowcount == 1`:
     ```sql
     UPDATE transactions SET status = 'ROLLING_BACK' WHERE tx_id = ? AND status = 'ABORTED_ELEVATION_REJECTED';
     UPDATE transaction_leases SET lease_token = ?, owner_pid = ?, status = 'LEASE_ACTIVE', updated_at = CURRENT_TIMESTAMP WHERE tx_id = ? AND status = 'LEASE_EXPIRED_UAC_REJECTED';
     ```
   - Выполняется `COMMIT` (`synchronous=FULL`). Если `rowcount != 1` — выход без модификаций.
   - Формируется новый `LockContext`. **Существование двух активных lease для одного `tx_id` аппаратно исключено.**
5. **Проверка привилегий:** Процесс подтверждает членство в группе Администраторов через `CheckTokenMembership(NULL, AdministratorsGroupSid)`.
6. **Независимость от секрета:** Приложение **не требует и не ищет старый `resume_secret`**. Легитимность процесса подтверждена наличием токена Администратора ОС Windows и эксклюзивным захватом `tx_<tx_id>.lock`.
7. **Отказ от автоматического продолжения:** Приложение **не продолжает установку автоматически**. Транзакция переведена в `ROLLING_BACK` для возврата игры в исходное чистое состояние.
8. **Выполнение Recovery:** Процедура Crash Recovery выполняет откат под защитой `LockContext.guard()` по полной Recovery Matrix (раздел 8.5) с подтверждением `VolumeSerialNumber`, `FileId` и хешей. При невозможности проверки — остановка со статусом `RECOVERY_CONFLICT`.

---

## 8. ТРАНЗАКЦИОННЫЙ ДВИЖОК FSM, АГРЕГАЦИЯ И CRASH RECOVERY

### 8.1. Разделение статусов
- **Статусы транзакции (FSM):** `CREATED`, `PREPARED`, `BACKUP_CREATED`, `INSTALLING`, `SUSPENDED_UAC_REQUIRED`, `ABORTED_ELEVATION_REJECTED`, `INSTALLED`, `COMMITTED`, `ROLLING_BACK`, `ROLLED_BACK`, `CRASH_RECOVERY`, `ABORTED_RECOVERED`, `RECOVERY_CONFLICT`.
- **Статусы отдельных файлов (`transaction_file_log`):** `PENDING`, `APPLIED`, `RESTORED`, `ALREADY_RESTORED`, `CONFLICT`.

### 8.2. Семантика статусов восстановления файлов
- **`RESTORED`:** Файловое действие по восстановлению (удаление созданного файла или возврат оригинала из бэкапа) было физически выполнено и подтверждено в текущей сессии Crash Recovery.
- **`ALREADY_RESTORED`:** При инспекции обнаружено, что целевое безопасное состояние уже достигнуто (файл отсутствует для `CREATE`, либо на диске подтверждён оригинал для `OVERWRITE`). Повторная запись не производится (идемпотентность).
- **`CONFLICT`:** Фактическое состояние файла противоречит журналу транзакции (неизвестный хеш, чужой `FileId`, повреждённый бэкап, сторонний файл на целевом пути, hard-link коллизия). Любые модификации файла категорически запрещены.

### 8.3. Правила агрегации состояний файлов в статус транзакции
Итоговый статус транзакции после завершения прохода Crash Recovery определяется детерминированным правилом агрегации состояний всех файлов транзакции $F = \{f_1, \dots, f_n\}$:

1. **Правило безусловного конфликта:** Если хотя бы один файл $f \in F$ получил статус `CONFLICT`, вся транзакция **безусловно переводится в статус `RECOVERY_CONFLICT`**. Никакой частичный успех не допускает перевод в безопасный терминальный статус.
2. **Правило полного восстановления:** Транзакция переводится в статус **`ABORTED_RECOVERED`** тогда и только тогда, когда **для всех файлов без исключения** ($\forall f \in F$) зафиксирован статус `RESTORED` либо `ALREADY_RESTORED`.
3. **Правило прерванного восстановления:** Если процесс аварийно завершился во время прохода Crash Recovery (часть файлов успела получить `RESTORED`, а часть осталась в `PENDING`/`APPLIED`), транзакция сохраняет статус **`CRASH_RECOVERY`** (или `ROLLING_BACK`). При следующем запуске цикл инспекции возобновляется.
4. **Правило недоступности БД:** Если база данных `state.db` повреждена (`SQLITE_CORRUPT`) или недоступна на чтение, транзакция принимает статус **`RECOVERY_CONFLICT`**, любые модификации диска блокируются.
5. **Правило отказа от UAC:** Если пользователь отказался от UAC после частичной установки, статус транзакции остаётся **`ABORTED_ELEVATION_REJECTED`**. Перевод в `ABORTED_RECOVERED` возможен только после успешного отката всех файлов в привилегированном режиме.

### 8.4. Протокол разрешения неопределённого SQLite COMMIT
Если при выполнении `COMMIT` в `state.db` вызов `sqlite3_step` возвращает ошибку (`SQLITE_BUSY`, `SQLITE_IOERR`) либо процесс падает во время записи кадра WAL, применяется строгий пошаговый протокол:

1. **Остановка файловых операций:** Все дальнейшие вызовы файлового движка по текущей транзакции немедленно блокируются.
2. **Завершение операторов и закрытие соединений:** Принудительно финализируются все активные `sqlite3_stmt` и закрываются соединения с `state.db` (`sqlite3_close_v2`).
3. **Повторное открытие и прогон WAL Recovery:** Вызывается `sqlite3_open_v2`. SQLite автоматически инспектирует журнал WAL и выполняет откат или фиксацию незавершённых кадров.
4. **Проверка целостности и доступности схемы:** Выполняются запросы `PRAGMA schema_version;` и `PRAGMA quick_check;`, проверяется читаемость таблиц `transactions`, `transaction_file_log`.
   - Если база повреждена (`SQLITE_CORRUPT`) или возвращает ошибку ввода-вывода — **база признаётся нечитаемой**. Запрещены любые изменения файлов игры, бэкапов и staging! Транзакция блокируется со статусом `RECOVERY_CONFLICT`.
5. **Поиск конкретной транзакции и записи файла:** В валидной `state.db` запрашивается запись для конкретного файла и `tx_id`:
   - **Случай A (Подтверждённый `PENDING`):** Запись физически присутствует в базе со статусом `PENDING`. SQLite зафиксировал кадр до сбоя. Применяется стандартная ветка Recovery Matrix для `PENDING`.
   - **Случай B (Подтверждённое отсутствие записи):** База данных полностью исправна (`quick_check == 'ok'`), таблица доступна, но запись о файловой операции отсутствует. **Условие трактовки:** файловый вызов перемещения физически инициируется **строго после** успешного возврата `SQLITE_DONE` от коммита `PENDING`. При подтверждённой целостности базы отсутствие записи означает, что файловая операция не могла быть начата. На диске игры изменений не производилось. Файл в staging очищается по правилам раздела 8.6, операция отменяется.
   - **Случай C (Неоднозначный результат):** Таблица заблокирована или возвращает ошибку чтения. Запрещены любые модификации файлов игры и staging. Статус переводится в `RECOVERY_CONFLICT`.

---

### 8.5. Исчерпывающая Recovery Matrix (CREATE и OVERWRITE)

Матрица охватывает все комбинации инспекции объектов целевого пути, backup и staging. Ни одно действие по удалению, перезаписи или восстановлению не выполняется без полной тройной проверки `(VolumeSerialNumber, FileId, SHA-256)` под защитой непрерывно удерживаемого `LockContext.guard()`.

#### Предварительный этап инспекции: Проверка аномалий Hard Link
До анализа таблицы для каждого файла транзакции выполняется инспекция с открытием дескрипторов существующих объектов с `dwShareMode = FILE_SHARE_READ` (`0x01`):
1. **Проверка Target-Backup:** Если дескрипторы Target и Backup открыты, и `target.Vol == backup.Vol` И `target.FileId == backup.FileId` $\implies$ **Аномалия Hard Link Target-Backup**.
2. **Проверка Target-Staging:** Если дескрипторы Target и Staging открыты, и `target.Vol == staging.Vol` И `target.FileId == staging.FileId` $\implies$ **Аномалия Hard Link Target-Staging**.
3. **Проверка Backup-Staging:** Если дескрипторы Backup и Staging открыты, и `backup.Vol == staging.Vol` И `backup.FileId == staging.FileId` $\implies$ **Аномалия Hard Link Backup-Staging**.
**При обнаружении любой из трёх аномалий Hard Link любые файловые действия категорически запрещены. Статус файла фиксируется как `CONFLICT`, транзакция переводится в `RECOVERY_CONFLICT`.**

#### Таблица детерминированных исходов Recovery Matrix (78 строк)

| № | Действие | Статус журнала | Инспекция Target | Инспекция Backup | Инспекция Staging | Разрешённое действие при Recovery под LockContext | Статус файла | Итоговый статус FSM |
|:---:|---|---|---|---|---|---|---|---|
| 1 | **CREATE** | `PENDING` | `OBJECT_NOT_FOUND` | `OBJECT_NOT_FOUND` | `VERIFIED_OBJECT` | Операция не началась. Handle-based удаление staging-файла (раздел 8.6). | `ALREADY_RESTORED` | По правилу 8.3 |
| 2 | **CREATE** | `PENDING` | `OBJECT_NOT_FOUND` | `OBJECT_NOT_FOUND` | `OBJECT_IDENTITY_MISMATCH` | На пути staging чужой объект. Удаление staging запрещено! | `CONFLICT` | `RECOVERY_CONFLICT` |
| 3 | **CREATE** | `PENDING` | `OBJECT_NOT_FOUND` | `OBJECT_NOT_FOUND` | `HASH_MISMATCH` | Staging повреждён или изменён. Удаление staging запрещено! | `CONFLICT` | `RECOVERY_CONFLICT` |
| 4 | **CREATE** | `PENDING` | `OBJECT_NOT_FOUND` | `OBJECT_NOT_FOUND` | `ACCESS_DENIED` | Невозможно проверить staging из-за прав доступа. Запрет изменений диска. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 5 | **CREATE** | `PENDING` | `OBJECT_NOT_FOUND` | `OBJECT_NOT_FOUND` | `SHARING_VIOLATION` | Staging занят сторонним процессом. Запрет изменений диска. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 6 | **CREATE** | `PENDING` | `OBJECT_NOT_FOUND` | `OBJECT_NOT_FOUND` | `IO_ERROR` | Ошибка ввода-вывода staging. Запрет изменений диска. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 7 | **CREATE** | `PENDING` | `OBJECT_NOT_FOUND` | `OBJECT_NOT_FOUND` | `OBJECT_NOT_FOUND` | Staging уже удалён. Целевой файл отсутствует. Идемпотентность. | `ALREADY_RESTORED` | По правилу 8.3 |
| 8 | **CREATE** | `PENDING` | `VERIFIED_OBJECT` (по `staging_file_id`) | `OBJECT_NOT_FOUND` | `OBJECT_NOT_FOUND` | Файл успел переместиться до фиксации APPLIED. Удаление цели через дескриптор. | `RESTORED` | По правилу 8.3 |
| 9 | **CREATE** | `PENDING` | `VERIFIED_OBJECT` (по `staging_file_id`) | `OBJECT_NOT_FOUND` | `VERIFIED_OBJECT` (по `staging_file_id`) | Аномалия Hard Link Target-Staging (отсекается на предпроверке). Запрет действий. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 10 | **CREATE** | `PENDING` | `VERIFIED_OBJECT` (по `staging_file_id`) | `OBJECT_NOT_FOUND` | `OBJECT_IDENTITY_MISMATCH` | Target перемещён, но в staging чужой объект. Удаление цели и staging запрещено! | `CONFLICT` | `RECOVERY_CONFLICT` |
| 11 | **CREATE** | `PENDING` | `VERIFIED_OBJECT` (по `staging_file_id`) | `OBJECT_NOT_FOUND` | `HASH_MISMATCH` | Target перемещён, но staging изменён. Запрет файловых действий. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 12 | **CREATE** | `PENDING` | `VERIFIED_OBJECT` (по `staging_file_id`) | `OBJECT_NOT_FOUND` | `ACCESS_DENIED` | Ошибка доступа к staging. Запрет действий до устранения блокировки. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 13 | **CREATE** | `PENDING` | `OBJECT_IDENTITY_MISMATCH` | `OBJECT_NOT_FOUND` | `OBJECT_NOT_FOUND` | **Чужой файл на целевом пути!** Удаление категорически запрещено независимо от хеша. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 14 | **CREATE** | `PENDING` | `OBJECT_IDENTITY_MISMATCH` | `OBJECT_NOT_FOUND` | `VERIFIED_OBJECT` | Чужой файл на целевом пути при наличии staging. Запрет изменений цели и staging! | `CONFLICT` | `RECOVERY_CONFLICT` |
| 15 | **CREATE** | `PENDING` | `HASH_MISMATCH` | `OBJECT_NOT_FOUND` | `OBJECT_NOT_FOUND` | Файл на целевом пути изменён извне. Удаление запрещено. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 16 | **CREATE** | `PENDING` | `HASH_MISMATCH` | `OBJECT_NOT_FOUND` | `VERIFIED_OBJECT` | Файл на целевом пути изменён, staging существует. Запрет удаления. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 17 | **CREATE** | `PENDING` | `ACCESS_DENIED` | `OBJECT_NOT_FOUND` | `VERIFIED_OBJECT` | Ошибка доступа к цели. Запрет трактовки как отсутствия, запрет действий. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 18 | **CREATE** | `PENDING` | `SHARING_VIOLATION` | `OBJECT_NOT_FOUND` | `VERIFIED_OBJECT` | Цель заблокирована другим процессом. Запрет действий. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 19 | **CREATE** | `PENDING` | `IO_ERROR` | `OBJECT_NOT_FOUND` | `VERIFIED_OBJECT` | Ошибка ввода-вывода цели. Запрет действий. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 20 | **CREATE** | `APPLIED` | `VERIFIED_OBJECT` (по `installed_file_id`) | `OBJECT_NOT_FOUND` | `OBJECT_NOT_FOUND` | Удаление созданного файла игры через проверенный дескриптор. | `RESTORED` | По правилу 8.3 |
| 21 | **CREATE** | `APPLIED` | `VERIFIED_OBJECT` (по `installed_file_id`) | `OBJECT_NOT_FOUND` | `VERIFIED_OBJECT` (по `installed_file_id`) | Аномалия Hard Link в CREATE APPLIED. Удаление запрещено! | `CONFLICT` | `RECOVERY_CONFLICT` |
| 22 | **CREATE** | `APPLIED` | `OBJECT_IDENTITY_MISMATCH` | `OBJECT_NOT_FOUND` | `OBJECT_NOT_FOUND` | Файл подменён сторонним процессом. Удаление запрещено. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 23 | **CREATE** | `APPLIED` | `HASH_MISMATCH` | `OBJECT_NOT_FOUND` | `OBJECT_NOT_FOUND` | Файл изменён сторонним процессом. Удаление запрещено. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 24 | **CREATE** | `APPLIED` | `OBJECT_NOT_FOUND` | `OBJECT_NOT_FOUND` | `OBJECT_NOT_FOUND` | Файл уже удалён в предыдущей итерации отката. Идемпотентность. | `ALREADY_RESTORED` | По правилу 8.3 |
| 25 | **CREATE** | `APPLIED` | `ACCESS_DENIED` | `OBJECT_NOT_FOUND` | `OBJECT_NOT_FOUND` | Ошибка доступа к целевому файлу. Запрет удаления до устранения блокировки. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 26 | **CREATE** | `APPLIED` | `SHARING_VIOLATION` | `OBJECT_NOT_FOUND` | `OBJECT_NOT_FOUND` | Целевой файл занят сторонним дескриптором. Запрет удаления. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 27 | **CREATE** | `APPLIED` | `IO_ERROR` | `OBJECT_NOT_FOUND` | `OBJECT_NOT_FOUND` | Ошибка ввода-вывода целевого файла. Запрет удаления. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 28 | **CREATE** | `RESTORED` | `OBJECT_NOT_FOUND` | `OBJECT_NOT_FOUND` | `OBJECT_NOT_FOUND` | Подтверждено отсутствие файла. Идемпотентность. | `ALREADY_RESTORED` | По правилу 8.3 |
| 29 | **CREATE** | `RESTORED` | `VERIFIED_OBJECT` | `OBJECT_NOT_FOUND` | `OBJECT_NOT_FOUND` | Файл появился снова после удаления. Запрет повторного удаления! | `CONFLICT` | `RECOVERY_CONFLICT` |
| 30 | **CREATE** | `RESTORED` | `ACCESS_DENIED` | `OBJECT_NOT_FOUND` | `OBJECT_NOT_FOUND` | Ошибка доступа к целевому пути. Запрет действий. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 31 | **OVERWRITE** | `PENDING` | `VERIFIED_OBJECT` (по `original_file_id`) | `OBJECT_NOT_FOUND` | `VERIFIED_OBJECT` | Эвакуация не началась. Оригинал на месте. Staging удаляется по дескриптору (раздел 8.6). | `ALREADY_RESTORED` | По правилу 8.3 |
| 32 | **OVERWRITE** | `PENDING` | `VERIFIED_OBJECT` (по `original_file_id`) | `OBJECT_NOT_FOUND` | `OBJECT_NOT_FOUND` | Эвакуация не началась. Оригинал на месте. Staging отсутствует. Идемпотентность. | `ALREADY_RESTORED` | По правилу 8.3 |
| 33 | **OVERWRITE** | `PENDING` | `VERIFIED_OBJECT` (по `original_file_id`) | `OBJECT_NOT_FOUND` | `OBJECT_IDENTITY_MISMATCH` | Оригинал на месте, но staging чужой. Staging не трогать. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 34 | **OVERWRITE** | `PENDING` | `VERIFIED_OBJECT` (по `original_file_id`) | `OBJECT_NOT_FOUND` | `HASH_MISMATCH` | Оригинал на месте, но staging изменён. Staging не трогать. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 35 | **OVERWRITE** | `PENDING` | `VERIFIED_OBJECT` (по `original_file_id`) | `OBJECT_NOT_FOUND` | `ACCESS_DENIED` | Оригинал на месте, ошибка доступа к staging. Запрет действий. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 36 | **OVERWRITE** | `PENDING` | `OBJECT_NOT_FOUND` | `VERIFIED_OBJECT` (по `original_file_id`) | `VERIFIED_OBJECT` | Сбой после эвакуации, до установки staging. Возврат бэкапа на целевой путь (`ReplaceIfExists=FALSE`). Удаление staging по 8.6. | `RESTORED` | По правилу 8.3 |
| 37 | **OVERWRITE** | `PENDING` | `OBJECT_NOT_FOUND` | `VERIFIED_OBJECT` (по `original_file_id`) | `OBJECT_NOT_FOUND` | Сбой после эвакуации, staging уже отсутствует. Возврат бэкапа на целевой путь (`ReplaceIfExists=FALSE`). | `RESTORED` | По правилу 8.3 |
| 38 | **OVERWRITE** | `PENDING` | `OBJECT_NOT_FOUND` | `HASH_MISMATCH` | `OBJECT_NOT_FOUND` | **Бэкап повреждён на диске!** Восстановление испорченного бэкапа запрещено. Staging не трогать. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 39 | **OVERWRITE** | `PENDING` | `OBJECT_NOT_FOUND` | `HASH_MISMATCH` | `VERIFIED_OBJECT` | Бэкап повреждён при наличии staging. Восстановление запрещено! Staging не трогать. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 40 | **OVERWRITE** | `PENDING` | `OBJECT_NOT_FOUND` | `OBJECT_IDENTITY_MISMATCH` | `OBJECT_NOT_FOUND` | **Чужой файл в бэкапе!** Восстановление чужого файла запрещено. Staging не трогать. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 41 | **OVERWRITE** | `PENDING` | `OBJECT_NOT_FOUND` | `OBJECT_IDENTITY_MISMATCH` | `VERIFIED_OBJECT` | Чужой файл в бэкапе при наличии staging. Восстановление и удаление запрещены! | `CONFLICT` | `RECOVERY_CONFLICT` |
| 42 | **OVERWRITE** | `PENDING` | `OBJECT_NOT_FOUND` | `ACCESS_DENIED` | `OBJECT_NOT_FOUND` | Ошибка доступа к бэкапу. Запрет файловых действий. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 43 | **OVERWRITE** | `PENDING` | `OBJECT_NOT_FOUND` | `SHARING_VIOLATION` | `OBJECT_NOT_FOUND` | Бэкап занят сторонним дескриптором. Запрет файловых действий. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 44 | **OVERWRITE** | `PENDING` | `OBJECT_NOT_FOUND` | `IO_ERROR` | `OBJECT_NOT_FOUND` | Ошибка ввода-вывода бэкапа. Запрет файловых действий. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 45 | **OVERWRITE** | `PENDING` | `OBJECT_NOT_FOUND` | `OBJECT_NOT_FOUND` | `OBJECT_NOT_FOUND` | Целевого файла нет и бэкап отсутствует. Восстановление невозможно. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 46 | **OVERWRITE** | `PENDING` | `OBJECT_NOT_FOUND` | `OBJECT_NOT_FOUND` | `VERIFIED_OBJECT` | Цели нет, бэкапа нет, но staging есть. Восстановление оригинала невозможно. Staging не трогать. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 47 | **OVERWRITE** | `PENDING` | `VERIFIED_OBJECT` (по `staging_file_id`) | `VERIFIED_OBJECT` (по `original_file_id`) | `OBJECT_NOT_FOUND` | Сбой после перемещения staging, до APPLIED. Удаление staging с целевого пути, возврат бэкапа на место (`ReplaceIfExists=FALSE`). | `RESTORED` | По правилу 8.3 |
| 48 | **OVERWRITE** | `PENDING` | `VERIFIED_OBJECT` (по `staging_file_id`) | `HASH_MISMATCH` | `OBJECT_NOT_FOUND` | Целевой staging установлен, но бэкап повреждён! Удаление цели уничтожит данные. Запрет действий. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 49 | **OVERWRITE** | `PENDING` | `VERIFIED_OBJECT` (по `staging_file_id`) | `OBJECT_IDENTITY_MISMATCH` | `OBJECT_NOT_FOUND` | В бэкапе чужой объект. Удаление цели запрещено. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 50 | **OVERWRITE** | `PENDING` | `VERIFIED_OBJECT` (по `staging_file_id`) | `OBJECT_NOT_FOUND` | `OBJECT_NOT_FOUND` | Целевой staging установлен, но бэкап утрачен! Удаление цели уничтожит данные. Запрет действий. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 51 | **OVERWRITE** | `PENDING` | `OBJECT_IDENTITY_MISMATCH` | `VERIFIED_OBJECT` (по `original_file_id`) | `OBJECT_NOT_FOUND` | Целевое имя занято сторонним файлом! Не перезаписывать! Оригинал сохранён в бэкапе. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 52 | **OVERWRITE** | `PENDING` | `HASH_MISMATCH` (по `staging_file_id`) | `VERIFIED_OBJECT` (по `original_file_id`) | `OBJECT_NOT_FOUND` | Установленный staging-файл изменён на диске игры. Удаление запрещено. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 53 | **OVERWRITE** | `PENDING` | `ACCESS_DENIED` | `VERIFIED_OBJECT` (по `original_file_id`) | `OBJECT_NOT_FOUND` | Ошибка доступа к цели. Запрет действий. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 54 | **OVERWRITE** | `PENDING` | `SHARING_VIOLATION` | `VERIFIED_OBJECT` (по `original_file_id`) | `OBJECT_NOT_FOUND` | Цель заблокирована сторонним процессом. Запрет действий. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 55 | **OVERWRITE** | `PENDING` | `IO_ERROR` | `VERIFIED_OBJECT` (по `original_file_id`) | `OBJECT_NOT_FOUND` | Ошибка ввода-вывода цели. Запрет действий. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 56 | **OVERWRITE** | `APPLIED` | `VERIFIED_OBJECT` (по `installed_file_id`) | `VERIFIED_OBJECT` (по `original_file_id`) | `OBJECT_NOT_FOUND` | Штатный откат: удаление установленного файла через дескриптор, возврат бэкапа на целевой путь (`ReplaceIfExists=FALSE`). | `RESTORED` | По правилу 8.3 |
| 57 | **OVERWRITE** | `APPLIED` | `VERIFIED_OBJECT` (по `installed_file_id`) | `HASH_MISMATCH` | `OBJECT_NOT_FOUND` | Бэкап повреждён. Возврат оригинала невозможен. Запрет удаления целевого файла. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 58 | **OVERWRITE** | `APPLIED` | `VERIFIED_OBJECT` (по `installed_file_id`) | `OBJECT_IDENTITY_MISMATCH` | `OBJECT_NOT_FOUND` | В бэкапе чужой объект. Запрет удаления целевого файла. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 59 | **OVERWRITE** | `APPLIED` | `VERIFIED_OBJECT` (по `installed_file_id`) | `OBJECT_NOT_FOUND` | `OBJECT_NOT_FOUND` | Бэкап утрачен. Возврат оригинала невозможен. Запрет удаления целевого файла. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 60 | **OVERWRITE** | `APPLIED` | `HASH_MISMATCH` (по `installed_file_id`) | `VERIFIED_OBJECT` (по `original_file_id`) | `OBJECT_NOT_FOUND` | Установленный файл изменён сторонним процессом после установки. Запрет удаления. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 61 | **OVERWRITE** | `APPLIED` | `OBJECT_IDENTITY_MISMATCH` | `VERIFIED_OBJECT` (по `original_file_id`) | `OBJECT_NOT_FOUND` | На целевом пути посторонний объект. Запрет удаления и перезаписи. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 62 | **OVERWRITE** | `APPLIED` | `ACCESS_DENIED` | `VERIFIED_OBJECT` (по `original_file_id`) | `OBJECT_NOT_FOUND` | Ошибка доступа к цели. Запрет действий до устранения блокировки. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 63 | **OVERWRITE** | `APPLIED` | `SHARING_VIOLATION` | `VERIFIED_OBJECT` (по `original_file_id`) | `OBJECT_NOT_FOUND` | Цель заблокирована сторонним процессом. Запрет действий. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 64 | **OVERWRITE** | `APPLIED` | `IO_ERROR` | `VERIFIED_OBJECT` (по `original_file_id`) | `OBJECT_NOT_FOUND` | Ошибка ввода-вывода цели. Запрет действий. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 65 | **OVERWRITE** | `APPLIED` | `VERIFIED_OBJECT` (по `original_file_id`) | `OBJECT_NOT_FOUND` | `OBJECT_NOT_FOUND` | Оригинал уже был возвращён на место в упавшей итерации отката. Идемпотентность. | `ALREADY_RESTORED` | По правилу 8.3 |
| 66 | **OVERWRITE** | `APPLIED` | `VERIFIED_OBJECT` (по `original_file_id`) | `OBJECT_NOT_FOUND` | `VERIFIED_OBJECT` | Оригинал на месте, но staging всё ещё существует. Staging удаляется по дескриптору (раздел 8.6). | `ALREADY_RESTORED` | По правилу 8.3 |
| 67 | **OVERWRITE** | `APPLIED` | `OBJECT_NOT_FOUND` | `VERIFIED_OBJECT` (по `original_file_id`) | `OBJECT_NOT_FOUND` | Установленный файл уже удалён, возврат бэкапа на целевой путь (`ReplaceIfExists=FALSE`). | `RESTORED` | По правилу 8.3 |
| 68 | **OVERWRITE** | `APPLIED` | `OBJECT_NOT_FOUND` | `VERIFIED_OBJECT` (по `original_file_id`) | `VERIFIED_OBJECT` | Цель пуста, бэкап подтверждён, staging ещё цел. Возврат бэкапа на место (`ReplaceIfExists=FALSE`), удаление staging по 8.6. | `RESTORED` | По правилу 8.3 |
| 69 | **OVERWRITE** | `APPLIED` | `OBJECT_NOT_FOUND` | `HASH_MISMATCH` | `OBJECT_NOT_FOUND` | Целевого файла нет и бэкап повреждён. Восстановление невозможно. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 70 | **OVERWRITE** | `APPLIED` | `OBJECT_NOT_FOUND` | `OBJECT_IDENTITY_MISMATCH` | `OBJECT_NOT_FOUND` | Целевого файла нет и бэкап подменён. Восстановление невозможно. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 71 | **OVERWRITE** | `APPLIED` | `OBJECT_NOT_FOUND` | `OBJECT_NOT_FOUND` | `OBJECT_NOT_FOUND` | Целевого файла нет и бэкап утрачен. Восстановление невозможно. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 72 | **OVERWRITE** | `APPLIED` | `OBJECT_NOT_FOUND` | `ACCESS_DENIED` | `OBJECT_NOT_FOUND` | Ошибка доступа к бэкапу. Запрет файловых действий. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 73 | **OVERWRITE** | `RESTORED` | `VERIFIED_OBJECT` (по `original_file_id`) | `OBJECT_NOT_FOUND` | `OBJECT_NOT_FOUND` | Оригинал проверен и подтверждён на целевом пути. Идемпотентность. | `ALREADY_RESTORED` | По правилу 8.3 |
| 74 | **OVERWRITE** | `RESTORED` | `OBJECT_IDENTITY_MISMATCH` | `OBJECT_NOT_FOUND` | `OBJECT_NOT_FOUND` | Файл был восстановлен, но затем подменён сторонним процессом. Запрет перезаписи. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 75 | **OVERWRITE** | `RESTORED` | `HASH_MISMATCH` | `OBJECT_NOT_FOUND` | `OBJECT_NOT_FOUND` | Файл был восстановлен, но затем изменён сторонним процессом. Запрет перезаписи. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 76 | **OVERWRITE** | `RESTORED` | `OBJECT_NOT_FOUND` | `OBJECT_NOT_FOUND` | `OBJECT_NOT_FOUND` | Файл был восстановлен, но затем удалён сторонним процессом. Запрет действий. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 77 | **OVERWRITE** | `RESTORED` | `ACCESS_DENIED` | `OBJECT_NOT_FOUND` | `OBJECT_NOT_FOUND` | Ошибка доступа к восстановленному файлу. Запрет действий. | `CONFLICT` | `RECOVERY_CONFLICT` |
| 78 | **UNMATCHED_TUPLE** | Любой статус FSM | Любой исход инспекции Target | Любой исход инспекции Backup | Любой исход инспекции Staging | **Универсальный отказ Fail-Closed:** любая комбинация, не вошедшая в строки 1–77. Файловые модификации категорически запрещены! | `CONFLICT` | `RECOVERY_CONFLICT` |

---

### 8.6. Алгоритм и правила очистки Staging при OVERWRITE
Очистка staging-файла при установке и аварийном восстановлении подчиняется строгому регламенту и полностью согласована с Recovery Matrix:

1. **Критерии разрешения удаления:** Удаление файла из `staging` разрешено тогда и только тогда, когда инспекция вернула статус `VERIFIED_OBJECT`:
   - `staging.VolumeSerialNumber == volume_serial` (файл находится на том же томе);
   - `staging.FileId == staging_file_id` (совпадает 128-битный индекс MFT);
   - `SHA256(staging) == target_sha256` (содержимое не повреждено и не подменено).
2. **Точки очистки при штатной работе:**
   - После успешного перемещения staging-файла в целевой путь вызовом `SetFileInformationByHandle` файл физически перестаёт существовать в `staging` (он перемещён в MFT, а не скопирован). Очистка не требуется.
3. **Поведение при сбоях и Crash Recovery под `LockContext.guard()`:**
   - **Сбой до эвакуации оригинала (Шаг 1–2):** Staging-файл проверяется по критериям п. 1 и удаляется через открытый проверенный дескриптор.
   - **Сбой после эвакуации, до перемещения staging (Шаг 3–6):** Staging-файл сохраняется до подтверждения возврата оригинала из бэкапа в целевой путь; после фиксации `RESTORED` оригинала staging-файл проверяется по п. 1 и удаляется.
   - **Сбой в статусах `SUSPENDED_UAC_REQUIRED` и `ABORTED_ELEVATION_REJECTED`:** Неиспользованные staging-файлы **не удаляются**, чтобы повышенный процесс мог продолжить установку без повторного скачивания из сети.
   - **Обнаружение постороннего объекта:** Если по пути staging обнаружен объект, инспекция которого вернула `OBJECT_IDENTITY_MISMATCH`, `HASH_MISMATCH`, `ACCESS_DENIED`, `SHARING_VIOLATION` или `IO_ERROR` — **любые действия с этим объектом категорически запрещены**. Файл не удаляется, транзакция фиксирует `CONFLICT` $\rightarrow$ `RECOVERY_CONFLICT`.

---

## 9. ПОИСК ИГРЫ И ГЛУБОКИЙ АНАЛИЗАТОР УСТАНОВЛЕННЫХ DLC

1. **Механизм поиска The Sims 4:**
   - Поиск ключей реестра: `SOFTWARE\Maxis\The Sims 4` и `SOFTWARE\Electronic Arts\EA Games\The Sims 4` (в ветках `HKLM` и `HKCU`, включая `WOW6432Node`).
   - Парсинг файла `libraryfolders.vdf` установленного клиента Steam (поиск AppID `1222670`).
   - Сканирование стандартных директорий Origin Games и EA Desktop.
   - Сканирование локальных дисковых томов с обязательной верификацией файловой системы NTFS через `GetVolumeInformationW`.
   - Возможность ручного выбора папки пользователем с валидацией структуры и файловой системы.
2. **Исключение зависимости от лаунчеров:**
   - Поиск реестра и каталогов лаунчеров используется **исключительно** для автоматического вычисления расположения папки с игрой на диске.
   - Приложение не выполняет проверок подлинности учётных записей лаунчеров, наличия лицензий или подписок и не вмешивается в механизмы лицензирования лаунчеров, осуществляя файловые операции напрямую в каталоге игры.
3. **Глубокий анализатор папок DLC:**
   - Наличие пустой папки (например, `EP01`) не считается признаком установленного дополнения.
   - Выполняется рекурсивное сканирование содержимого папки дополнения на фактическое присутствие основных файлов игровых архивов `.package` с ненулевым размером.

---

## 10. МОДУЛЬ «DLC ПОМОЩНИК» (POST INSTALL HELPER)

1. **Источник архива:** Публичный URL `https://disk.yandex.ru/d/RgVixKr-6QlLsQ` (`dlc помощник.zip`).
2. **Верификация доверия (Zero Blind Trust):**
   - Название архива или URL не являются доказательством доверия.
   - Эталонный отпечаток архива `expected_helper_sha256` объявляется в `store.json` и защищается подписью в `release-manifest.json`.
   - Несовпадение SHA-256 скачанного архива блокирует распаковку.
3. **Белый список типов файлов:**
   - Для обычных пакетов DLC разрешены только игровые ресурсы (`.package`, `.ini`, манифесты и языковые пакеты); исполняемые файлы (`.exe`, `.bat`, `.cmd`, `.dll`, `.vbs`, `.ps1`) в DLC-архивах категорически блокируются.
   - Для архива помощника из белого списка разрешена распаковка бинарных файлов утилиты (`.exe`, `.bat`, `.cmd`, `.dll`) непосредственно в корень установленной игры.
4. **Поиск исполняемого файла:**
   - Модуль выполняет поиск исполняемого файла утилиты среди распакованных файлов помощника, исключая штатные исполняемые файлы игры (`TS4_x64.exe`, `TS4.exe`).
5. **Явное подтверждение пользователя (User Consent Gate):**
   - Автоматический скрытый запуск бинарников запрещён.
   - Пользователю отображается модальное окно с указанием пути к запускаемому файлу, его SHA-256 и явным запросом разрешения на запуск.
   - Запуск через `subprocess.Popen` инициируется только после нажатия пользователем кнопки подтверждения, с обязательной установкой рабочего каталога `cwd = game_root`.

---

## 11. ГРАФИЧЕСКИЙ ИНТЕРФЕЙС CUSTOMTKINTER

### 11.1. Концепция и навигация
Интерфейс строится по схеме Single Window с боковой панелью (Sidebar) и адаптивным контейнером представлений (Views):
1. **Главная (Дашборд):** Статус облака Яндекс.Диска, статус и версия игры The Sims 4, готовность утилиты помощника, сводные карточки метрик, кнопки быстрой пакетной установки.
2. **Каталог архивов и DLC:** Таблица дополнений с быстрым поиском, фильтрами («Все», «Установленные», «Не установленные», «Требуют проверки»), отображением размеров в архиве и в распакованном виде.
3. **Загрузки и очередь установки:** Карточки активных фоновых потоков загрузки с индикацией скорости, оставшегося времени (ETA), размера .part-файла и статуса возобновления HTTP Range 206.
4. **Инспектор архивов, Dry Run и аудит:** Просмотр структуры архивов без распаковки, детальный отчёт симуляции Dry Run (список создаваемых/заменяемых файлов без записи на диск).
5. **Резервные копии и откат:** Список точек отката, создание пользовательских бэкапов `Saves`, `Tray`, `Mods`, кнопка безопасного отката последней транзакции.
6. **История операций и журнал транзакций:** Пофайловый аудит выполненных изменений со статусами коммитов.
7. **Пользовательские настройки:** Управление лимитами загрузки, подтверждениями, просмотр информации о магазине.

---

## 12. ТЕСТИРОВАНИЕ И ПРИЁМОЧНЫЕ КРИТЕРИИ

### 12.1. Регламент тестирования
- Тесты выполняются **после каждого этапа разработки** на изолированных временных каталогах `tempfile.TemporaryDirectory`.
- Переход к следующему этапу возможен только при 100% успешном завершении тестов текущего этапа.
- На Этапе 9 проводится комплексное сквозное тестирование всей системы.

### 12.2. Критерии приёмки White Label
1. Из единой кодовой базы собираются независимые автономные бинарники для каждого магазина.
2. Сборки магазинов имеют различные названия, логотипы, иконки, цветовые палитры и URL источников.
3. Покупательский EXE не содержит кода утилит сборщика `owner_tools` и экрана администрирования.
4. Изменение конфигурации одного магазина не требует пересборки других магазинов.
5. Данные магазинов изолированы в каталоге `%LOCALAPPDATA%`.
6. Каждая сборка валидируется через `release-manifest.json` с проверкой подписи Ed25519.
