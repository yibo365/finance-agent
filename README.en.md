# finance-agent

[中文](README.md) ｜ **English**

An investment-research agent workbench: describe a research task in natural language, and it autonomously runs the full pipeline — market data collection, change-point detection, event research, changepoint×event alignment — producing **interactive, fully-traceable** HTML / Excel / PPT / Word artifacts. Comes with a ChatGPT-style local web UI: parallel sessions, live execution timeline, and multi-turn incremental edits to artifacts (all versions preserved).

Built on the **OpenAI Agents SDK** with three cooperating layers: tools (deterministic capabilities), subagents (isolated-context judgment units), and skills (methodology + rendering-skeleton assets with a custom loader). Every conclusion links back to evidence. Works with **any OpenAI-compatible LLM provider** (OpenAI / OpenRouter / DeepSeek / Kimi / self-hosted gateways) — provider quirks are absorbed by a single compatibility layer.

**Docs** (Chinese): [Product](docs/product.md) | [Technical](docs/technical.md) | [Architecture](docs/architecture.md) | [AI-assisted development process](docs/ai-process.md)

## Quick start

```bash
# Requirements: uv (https://docs.astral.sh/uv/) + Node.js (frontend build)
uv sync

# Configure (or skip: fill in via the "Settings" dialog in the web UI later,
# which writes back to .env automatically)
cp .env.example .env    # set OPENAI_API_KEY (+ OPENAI_BASE_URL for gateways), TAVILY_API_KEY

# Start (dev mode: backend + frontend in one command)
./scripts/dev.sh        # open http://127.0.0.1:5173
```

Production mode (build the frontend once, then run the backend only):

```bash
npm --prefix webapp install && npm --prefix webapp run build
uv run finance-agent --web    # open http://127.0.0.1:8765
```

## Usage

Type a research task into the web UI, e.g.:

> Review NVDA's price history over the past five years, curate the major AI-industry events of the same period (ChatGPT launch, B100, DeepSeek, ...), mark the events and impact ratings at each price change-point on an interactive candlestick chart, and generate a self-contained, traceable HTML report.

> Build an interactive comparative analysis of gold vs. bitcoin as safe-haven / inflation-hedge assets; deliver an Excel backtest workbook, a PPT decision framework, and a Word strategy report.

- **Live execution timeline** — every search, computation, and render step from each agent streams into the UI;
- **Parallel sessions** — run tasks in multiple sessions simultaneously; switching never interrupts execution; one-click stop (a real cancellation, down to the SDK run);
- **Multi-turn edits** — "raise the DeepSeek event's impact rating to high" performs a targeted spec change and re-render; all versions are kept;
- **Artifacts panel** — preview/download in the right column; files also live under `outputs/<session-id>/artifacts/` (HTML is self-contained and opens offline), with provenance in `evidence.json`.

Optional CLI: `uv run finance-agent` (REPL); `-p "task…"` (one-shot); `--list-sessions` / `--resume <id>`.

## Configuration

| Variable | Purpose |
| --- | --- |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `FINANCE_AGENT_MODEL` | Any OpenAI-compatible provider triple; empty base URL = official OpenAI |
| `TAVILY_API_KEY` | Web search (a deterministic search API, never paraphrased by an LLM — switching models never changes retrieved data); falls back to HN/Yahoo when unset |
| `FINANCE_AGENT_MOCK=1` | Offline mock: bundled NVDA seed data + offline news fixtures |

Full reference (search budget, output caps, JSON mode, …): [Technical doc §Configuration](docs/technical.md#4-配置).

## Tests

```bash
uv run pytest                  # full Python suite (no real network / LLM calls)
uv run ruff check .            # lint
node --test tests/*.test.cjs   # frontend asset tests
FINANCE_AGENT_MOCK=1 uv run pytest tests/test_agents.py   # offline smoke
```

## Security notes

- Secrets are injected via env vars / `.env` only — never committed, never embedded in artifacts; the settings API masks them;
- The web server binds to 127.0.0.1 only (single-user local tool);
- Market/news sources are key-free and reproducible (Yahoo Chart, HN Algolia); HTML artifacts make zero external requests (Plotly inlined);
- In lieu of an OS sandbox: agents get no generic file tools, tool parameters are logical identifiers only, and all derived paths are confined to the session workspace (see [Architecture §8](docs/architecture.md)).
