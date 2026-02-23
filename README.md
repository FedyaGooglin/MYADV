# classifieds-hub

Aggregator for local classifieds with a Telegram digest.

Architecture map (keep updated): `CODEMAP.md`

MVP focus:
- Source: `aykhal.info`
- Cities: `Aykhal`, `Udachny`
- Schedule: `09:00`, `14:00`, `18:00`, `22:00` (`Asia/Yakutsk`)

## Stack

- Python 3.11+
- `httpx` + `beautifulsoup4` for web collection/parsing
- `SQLAlchemy` + `aiosqlite` for storage
- `apscheduler` for timed runs
- `aiogram` for Telegram bot delivery
- `pytest` for tests, `ruff` + `mypy` for quality checks

## Quick start

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .[dev]
cp .env.example .env
```

Run app bootstrap:

```bash
python -m classifieds_hub
```

Run one Aykhal ingest pass:

```bash
python -m classifieds_hub.collectors.run_once
```

Run Telegram chat ingest pass (Client API):

```bash
python -m classifieds_hub.collectors.tg_chat
```

Authorize Telegram Client API session (first run):

```bash
python -m classifieds_hub.collectors.tg_auth
```

If code delivery is stuck, reset session and auth again:

```bash
python -m classifieds_hub.collectors.tg_auth
# then choose action: reset
python -m classifieds_hub.collectors.tg_auth
```

Run Telegram bot polling:

```bash
python -m classifieds_hub.bot.app
```

Run tests:

```bash
pytest
```

Lint / format / type-check:

```bash
ruff check .
ruff format .
mypy src
```
