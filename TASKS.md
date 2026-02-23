# Classifieds Hub - Task Plan

## Scope Locked For MVP (Iteration 1)

- Source: `aykhal.info` only
- Cities: `Aykhal`, `Udachny`
- Delivery: Telegram bot
- Schedule: `09:00, 14:00, 18:00, 22:00` (`Asia/Yakutsk`)
- Digest format: extended (title + short description + price + link)
- Categories: start from site categories, then trim ultra-niche categories after review
- Listing expiry: объявления старше 30 дней помечаются как `expired` и скрываются из выдачи бота

---

## Milestone 0 - Project Bootstrap

- [x] Initialize Python project structure
- [x] Add `.env.example` with required variables
- [x] Add `pyproject.toml` / dependencies
- [x] Add logging setup
- [x] Add local run commands in `README.md`

Deliverable: project starts locally and prints healthy startup logs.

## Milestone 1 - SQLite Schema + Storage Layer

- [x] Create SQLite database file and migration/init script
- [x] Create tables:
  - [x] `sources`
  - [x] `listings`
  - [x] `listing_media`
  - [x] `runs`
  - [x] `subscriptions`
  - [x] `delivery_log`
- [x] Add indexes for dedup and query speed
- [x] Implement repository methods for insert/upsert/search

Deliverable: DB schema ready, unique constraints prevent duplicate listings.

## Milestone 2 - Aykhal.info Collector (Incremental)

- [x] Implement board listing fetcher
- [x] Parse item fields:
  - [x] published date
  - [x] title
  - [x] description
  - [x] price
  - [x] city/location
  - [x] category
  - [x] URL
- [x] Add incremental strategy:
  - [x] track `external_id`/URL per source
  - [x] stop early when old known items appear
- [x] Add retry + timeout + safe backoff

Deliverable: each run ingests only new posts from `aykhal.info`.

## Milestone 3 - Normalization + Dedup

- [x] Normalize city names (`Aykhal`, `Udachny`)
- [x] Normalize prices/currency
- [x] Normalize categories from source labels
- [x] Add hard dedup key (`source + external_id`)
- [x] Add soft dedup hash (`title + price + phone + city`)
- [x] Mark listings older than 30 days as expired
- [x] Exclude expired listings from default feed queries

Deliverable: duplicates are not re-sent to users.

## Milestone 4 - Telegram Bot (MVP)

- [ ] Implement bot commands:
  - [x] `/search` (filters via menu/buttons)
  - [x] `/categories` (board entrypoint)
  - [x] `/subscribe` (save user filters)
  - [x] `/unsubscribe`
- [x] Category + city browsing with pagination (5 items/page)
- [x] Disable global feed flow (board only via category selection)
- [x] Add main menu buttons (`Категории`, `Подписки`, `Помощь`)
- [x] Add UX controls (`/menu`, `/cancel`, "В меню" button)
- [x] Add subscriptions screen with active filters and "disable all"
- [x] Add delivery log to avoid repeated sends

Deliverable: user can browse and subscribe to filtered listing updates.

## Milestone 5 - Scheduler + Runs

- [ ] Add scheduler (cron/systemd or APScheduler)
- [ ] Configure 4 daily runs at Yakutsk timezone
- [ ] Persist run status (`started`, `ok`, `failed`, counts)
- [ ] Add basic alerting/log summary on failures

Deliverable: autonomous daily pipeline with traceable run history.

## Milestone 6 - Category Review Pass

- [ ] Export discovered source categories from collected data
- [ ] Review with product owner (you)
- [ ] Create final allowlist/denylist
- [ ] Apply category filter in collection and digest

Deliverable: irrelevant ultra-niche categories excluded.

---

## Post-MVP Backlog (Iteration 2+)

### B1 - Telegram Chats/Channels Scraping

- [x] Add Telegram source adapter (client API)
- [x] Parse text, links, phone, price, location hints
- [x] Classify posts into normalized categories
- [x] Dedup against website listings

### B2 - Avito Fallback Adapter

- [ ] Add low-frequency fallback parser for latest listings only
- [ ] Reuse same normalization + dedup pipeline

### B3 - MAX Delivery Channel

- [ ] Add MAX notifier adapter
- [ ] Reuse subscription model and digest formatter

---

## Acceptance Criteria For Iteration 1

- [ ] Telegram digest sends at `09/14/18/22` Yakutsk time
- [ ] Digest contains date, title, short description, price, and link
- [x] New `aykhal.info` listings appear in SQLite within scheduled run
- [x] No duplicate listings after repeated runs
- [x] City/category filters work for `Aykhal` and `Udachny`

## Maintenance Rule

- [x] Keep `CODEMAP.md` up to date as architecture changes
