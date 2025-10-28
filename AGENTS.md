# Repository Guidelines

AITradeGame is a Flask-based trading simulator that mixes API-driven back-end logic with a small HTML/JS front end. Follow the guidance below to stay aligned with existing patterns and keep contributions easy to review.

## Project Structure & Module Organization
- `app.py` starts the Flask app and wires endpoints to business logic in `trading_engine.py`, `market_data.py`, and `ai_trader.py`.
- `database.py` manages the SQLite store (`AITradeGame.db` by default); update schema changes here and in any migrations you introduce.
- Web assets live in `templates/index.html` and `static/{app.js,style.css}`; keep new assets grouped by feature.
- Environment samples belong in `config.example.py`; copy to `config.py` if you need a local override that is not checked in.

## Build, Test, and Development Commands
- Set up dependencies: `python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`.
- Run the app locally: `python app.py` (serves http://localhost:5000).
- Docker workflow: `docker-compose up -d` to start, `docker-compose down` to stop and release the bound volume.
- Create a distributable binary: `pyinstaller app.py --name AITradeGame` (mirrors the existing release build pipeline).

## Coding Style & Naming Conventions
- Target Python 3.9+, following PEP 8 (4-space indentation, snake_case for modules/functions, CapWords classes).
- Prefer explicit imports from sibling modules (`from trading_engine import TradingEngine`) and keep configuration constants in uppercase.
- Run Black or an equivalent formatter before opening a PR; match the current line length (~100 chars) in Python and 2-space indentation in front-end assets.

## Testing Guidelines
- There is no automated suite yet; add `pytest`-based tests under a new `tests/` directory with files named `test_*.py`.
- Focus coverage on trading workflows, database operations, and API provider integrations; mock external API calls to keep tests deterministic.
- Use `pytest -q` locally and include log snippets or screenshots if manual verification is required for UI changes.

## Commit & Pull Request Guidelines
- Follow the observed Conventional Commits style (`feat:`, `docs:`, `fix:`) with concise summaries; add bullet points for notable subchanges when needed.
- Reference related issues in the PR body, describe functional impacts, and attach UI screenshots or API traces for visible changes.
- Ensure `docker-compose up` and `python app.py` still launch cleanly before requesting review; note any configuration steps reviewers must perform.
