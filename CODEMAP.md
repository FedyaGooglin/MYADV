# Code Map - classifieds-hub

Этот файл — карта проекта: что за что отвечает, как модули взаимодействуют,
и куда смотреть при доработках.

## Update Policy

- Обновлять `CODEMAP.md` после каждого закрытого milestone.
- Если добавлен новый модуль/поток данных — добавить его в разделы
  `Directory Map`, `Runtime Flows`, `Data Model`.
- В конце файла вести короткий `Change Log`.

## Directory Map

- `src/classifieds_hub/main.py`
  - bootstrap приложения
  - инициализация БД
  - базовый health-лог
- `src/classifieds_hub/core/config.py`
  - настройки из `.env`
  - парсинг CSV полей (`TARGET_CITIES`, `RUN_HOURS_LOCAL`)
- `src/classifieds_hub/core/logging.py`
  - единая настройка логирования

- `src/classifieds_hub/db/models.py`
  - SQLAlchemy модели (`sources`, `listings`, `runs`, ...)
  - индексы и unique-ограничения
- `src/classifieds_hub/db/session.py`
  - создание async engine/session factory
  - `init_db()` + легкая SQLite миграция
- `src/classifieds_hub/db/repository.py`
  - репозитории для `Source`, `Listing`, `Run`
  - upsert, выдача ленты, маркировка просроченных объявлений

- `src/classifieds_hub/collectors/aykhal.py`
  - парсер `aykhal.info`
  - инкрементальный сбор
  - нормализация полей
  - подсчет hash для мягкого дедупа
  - извлечение URL картинок объявления (если есть валидные)
- `src/classifieds_hub/collectors/run_once.py`
  - one-shot запуск коллектора (ручной/cron-режим)

- `tests/`
  - `test_smoke.py`: smoke импорт запуска
  - `test_config.py`: парсинг env-конфига
  - `test_db_repository.py`: репозитории/дедуп/expiry
  - `test_aykhal_parser.py`: парсинг карточек и списка

## Runtime Flows

## 1) Bootstrap Flow

1. `python -m classifieds_hub`
2. Загружается `Settings`
3. Настраивается logging
4. Инициализируется БД (`init_db`)
5. Печатается состояние окружения

## 2) Collection Flow (Aykhal)

1. `python -m classifieds_hub.collectors.run_once`
2. Создается `Run` со статусом `started`
3. Запрашивается страница `https://aykhal.info/board`
4. Извлекаются ссылки `/board/readXXXXX.html`
5. Идет обход карточек:
   - если карточка уже известна: считаем streak
   - если подряд >=2 известные карточки: ранняя остановка
6. Новые карточки парсятся и нормализуются
7. `ListingRepository.upsert(...)`
8. После обхода: `mark_expired()` (объявления старше 30 дней)
9. `Run` закрывается как `ok` или `failed`

## 3) Board Bot Flow (current)

1. Пользователь вызывает `/categories` (или `/search`)
   - либо нажимает кнопку главного меню `Категории`
2. Бот показывает категории из БД (`list_active_categories`)
3. Пользователь выбирает город (`Aykhal` / `Udachny` / любой)
4. Бот показывает объявления по выбранному фильтру
5. Пагинация по `5` объявлений (`новые -> старые`)
6. Из результата можно подписаться на фильтр (категория + город)
7. Главное меню доступно кнопками: `Категории`, `Подписки`, `Помощь`
8. Есть команды UX-контроля: `/menu`, `/cancel`

## Data Model (Key Tables)

- `sources`
  - справочник источников (`aykhal_info`, позже `avito`, `tg_channels`)

- `listings`
  - основная витрина объявлений
  - hard dedup:
    - unique (`source_id`, `external_id`)
    - unique (`source_id`, `url`)
  - soft dedup поле: `content_hash`
  - expiry:
    - `expires_at`
    - `is_expired`

- `runs`
  - история запусков collector pipeline
  - счетчики: `found_count/new_count/updated_count`

- `subscriptions`, `delivery_log`
  - заготовка для Telegram-рассылки

- `listing_media`
  - ссылки на изображения карточек объявлений
  - используется для вывода `Фото: ...` в компактной карточке

## Business Rules (Current)

- Время в БД хранится как timezone-aware UTC.
- Город нормализуется в `Aykhal` / `Udachny` (если распознано).
- Объявление считается просроченным через 30 дней.
- Просроченные объявления не должны попадать в выдачу бота.
- Общей ленты нет: пользователь всегда начинает с выбора категории.
- Push-режим существует только через подписки.
- Инкрементальный сбор останавливается рано, если пошла полоса старых карточек.

## Next Integration Points

- Telegram Bot layer:
  - use `ListingRepository.latest(...)`
  - use `subscriptions` + `delivery_log`
- Scheduler layer:
  - вызывать `collectors.run_once` по расписанию `09/14/18/22` Asia/Yakutsk

## Change Log

- 2026-02-21:
  - Добавлен bootstrap проекта и test/lint tooling.
  - Добавлен DB слой: модели, сессии, репозитории.
  - Добавлен `aykhal.info` collector с инкрементальным режимом.
  - Добавлены expiry-поля (`expires_at`, `is_expired`) и логика скрытия просроченных.
  - Добавлена эта карта кода (`CODEMAP.md`).
  - Добавлено главное меню-клавиатура в Telegram-боте (`Категории`, `Подписки`, `Помощь`).
  - Добавлен экран подписок с просмотром текущих фильтров и кнопкой "Отключить все подписки".
