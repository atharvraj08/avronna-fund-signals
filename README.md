# Avronna Fund Signals

## What it does

A prototype built for Avronna that produces a weekly ranked list of hedge funds and prop
trading firms showing signs that CPU/GPU speed is limiting their models: FPGA/ASIC hiring, GPU
cluster buildouts, colocation and microwave links, hardware leadership hires, and public statements about latency. Every fund in the list comes with its evidence (source URL, date, and a short verbatim
snippet), so Avronna's sales team can find the funds most likely to need custom low-latency chips.

## How it works

1. **Sources**: collect recent news articles (from RSS feeds or a news API), job posts (from Greenhouse, Lever, or a paid jobs API), and anything from your in-house scraper (any scraper you plug in; the tool runs it and analyzes what it finds). Everything goes into one list, with duplicates and old items removed.
2. **Fund matching**: check each article and job post against the universe (your list of hedge funds to track) to see which funds it mentions. It recognizes full names, nicknames and near-spellings, and avoids look-alikes such as "two sigma" the statistics term or The Citadel college. If an article mentions several funds, all of them are matched.
3. **Signal detection**: for each matched article, look for signs the fund needs faster computing, such as hiring FPGA engineers, building GPU clusters, or talking about latency, and save the exact quote as evidence. Right now this uses keyword rules only, which is a limitation: it can miss signals worded differently, and when an article names several funds, it credits all of them even if only one is doing the thing. The code has a slot ready for an AI model with reasoning abilities, which would fix both.
4. **Scoring**: give each fund a score based on how strong its signals are and how recent they are (newer counts more). The same story repeated across several sites counts once, and ties are always broken the same way.
5. **Report**: save a weekly report as `report.md`, `report.html`, `ranking.csv` and `signals.json`, and optionally send it by email or Slack.

```
 RSS / news / jobs / scraper
            │
            ▼
   [1] Sources ──► RawDocument
            │
            ▼
   [2] Fund matching ──► FundMatch
            │
            ▼
   [3] Signal detection ──► Signal (+ URL, date, snippet)
            │
            ▼
   [4] Scoring ──► FundScore (ranked)
            │
            ▼
   [5] Report ──► output/<week>/report.md|html, ranking.csv, signals.json
```

## Quickstart

```bash
git clone https://github.com/atharvraj08/avronna-fund-signals.git
cd avronna-fund-signals
make install          # creates .venv and installs the package with dev tools
make run              # demo mode: no keys needed, uses bundled fixtures
```

Output lands in `output/2026-10-19/` (the demo week). Open `report.html` in a browser or read
`report.md`. For a real week, copy `.env.example` to `.env`, fill in what you have, and run
`.venv/bin/fundsignals run --week 2026-10-26`. With no keys at all, the CLI falls back to demo mode.

## Configuration and placeholders

Secrets go in `.env` (only `.env.example` is committed). Everything else is in `config/settings.yaml`.
Every value below is a **placeholder** in this repo; nothing real is included.

| Name | What it's for | Required? | Where to get it |
|---|---|---|---|
| `LLM_API_KEY` | Turns on the optional LLM signal classifier | No | Your LLM provider's console |
| `LLM_MODEL` | Model name for the LLM classifier | No (only with `LLM_API_KEY`) | Your LLM provider's model list |
| `NEWS_API_KEY` | News API source (`NewsAPISource`) | No | Your licensed news API vendor |
| `JOBS_API_KEY` | Paid job-data API source | No | Your job-data vendor |
| `EXTERNAL_SCRAPER_MODULE` | Python module path of Avronna's in-house scraper, e.g. `mycorp.scraper` | No | Your internal codebase |
| `SMTP_HOST` | Mail server for emailing the report | No | Your email provider / IT |
| `SMTP_PORT` | Mail server port (default 587) | No | Your email provider / IT |
| `SMTP_USER` | Mail server username | No | Your email provider / IT |
| `SMTP_PASSWORD` | Mail server password or app password | No | Your email provider / IT |
| `REPORT_RECIPIENTS` | Comma-separated email addresses that get the report | No (needed for email) | Sales team |
| `SLACK_WEBHOOK_URL` | Incoming webhook that receives the top-5 summary | No | Slack app settings → Incoming Webhooks |
| `FUND_UNIVERSE_PATH` | CSV of funds to track (default `data/fund_universe.sample.csv`) | No | Your CRM / prospect list export |
| `sources.rss_feeds` (settings.yaml) | List of RSS feed URLs (32 placeholder `feeds.example.com` URLs) | Yes, for live news | Feeds your team licenses or trusts |
| `sources.greenhouse_boards`, `sources.lever_boards` (settings.yaml) | Job-board tokens per fund | No | The fund's careers page URL |
| `sources.news_api.endpoint`, `sources.jobs_api.endpoint` (settings.yaml) | Vendor API endpoints (placeholder `example.com`) | With the matching key | Your vendor's docs |
| `signals.llm.endpoint` (settings.yaml) | LLM HTTP endpoint (placeholder `llm.example.com`) | With `LLM_API_KEY` | Your LLM provider's docs |
| `scoring.weights`, `scoring.half_life_days` (settings.yaml) | Signal-type weights and recency half-life (21 days) | Yes (defaults provided) | Tune with the sales team |
| `sources.http.user_agent` (settings.yaml) | Contact string sent with HTTP requests | Yes, for live runs | Your team's contact address |

## Using real data

- **Real fund universe**: export your prospect list as CSV with columns
  `fund_id,name,aliases,website,hq` (aliases separated by `|`) and set `FUND_UNIVERSE_PATH`.
  Add Greenhouse/Lever tokens for those funds under `sources.greenhouse_boards` / `sources.lever_boards`.
- **In-house scraper**: set `EXTERNAL_SCRAPER_MODULE=yourpackage.scraper`. The module must expose
  `def fetch(since: datetime) -> list[dict]`. Each dict needs `url`, `title`, `text` and `published_at`
  (a datetime or ISO-8601 string), plus optional `source_type` (`news|job|filing`) and `source`.
  Errors in the scraper are logged and skipped, so they never crash the run.
- **LLM classifier**: set `LLM_API_KEY` and `LLM_MODEL`, and point `signals.llm.endpoint` at your
  provider (adapt `HttpxLLMClient` in `src/fundsignals/signals/llm.py` to its request shape). The model must
  return strict JSON `{"signals":[{"signal_type","strength","evidence"}]}`. Output that is malformed or not
  verbatim from the document is dropped.
- **Feeds**: replace the placeholder URLs in `sources.rss_feeds`. Fetching respects `robots.txt`,
  rate-limits per host, and retries with backoff.

## Running the tests

Tests never touch the network; everything runs on recorded fixtures in `tests/fixtures/`.

| Command | What it covers |
|---|---|
| `make test-sources` | RSS/job-board parsing, dedupe of known duplicates, failing/timeout sources, missing keys, `since` filtering, robots.txt, retry/backoff |
| `make test-entities` | Precision/recall on labeled fixtures, look-alike traps rejected, aliases resolve to one fund |
| `make test-signals` | Each signal type fires on its positives, nothing on negatives, mocked LLM incl. malformed JSON, every signal has URL + snippet |
| `make test-scoring` | Hand-computed scores, recency decay, duplicate capping, deterministic ranking and tie-breaks |
| `make test-report` | Golden-file markdown/CSV, every ranked fund has a source link, email/Slack mocked and skipped without keys |
| `make e2e` | Full demo pipeline: expected funds in the top 5, negatives absent, every signal traces to a fixture URL; env vars documented |
| `make test` / `make lint` | Whole suite with coverage / ruff |

Each `test-<agent>` target enforces at least 85% coverage on its package. CI runs lint and the full suite on every push and PR.

## Example output

From `make run` (demo fixtures, week of 2026-10-19):

```
 1. Jump Trading                     4.841  (3 signals)
 2. Two Sigma                        4.408  (3 signals)
 3. Hudson River Trading             4.276  (3 signals)
 4. Citadel Securities               2.791  (2 signals)
 5. XTX Markets                      1.994  (2 signals)
```

```markdown
## 1. Jump Trading (score 4.84)

**Why:** Hiring FPGA engineers; Senior hardware leadership hire; Colocation/network upgrades (3 signals)

- **hardware_leadership_hire** (2026-10-17): Jump Trading has appointed a former Nvidia vice president
  of GPU architecture as its new chief technology officer... [source](https://marketswire.example.com/...)
- **fpga_hiring** (2026-10-15): Jump Trading is looking for an ASIC design engineer with deep RTL
  experience in Verilog/VHDL... [source](https://boards.greenhouse.io/jumptrading/jobs/7002001)
```

(The demo data is fictional; the fixture URLs are illustrative.)

## Limits and responsible use

- **Signals are hints, not proof.** A job post or press quote suggests a compute bottleneck; it doesn't
  confirm budget, timing or intent. Treat the ranking as a starting point for research and outreach.
- **Keyword rules have blind spots.** Until an AI model is plugged in, signal detection relies on keyword rules:
  - **Wrong firm credited:** when an article names several funds, every one gets the signal, even if only one
    is doing the thing (e.g. "Jump hired a CTO who used to work at Citadel" also credits Citadel).
  - **Missed signals:** signals worded differently from the rules are not picked up.
  - **No negation:** a sentence like "we are not hiring FPGA engineers" can still count as a signal.
  - **Look-alike names:** only a few look-alikes are guarded against (e.g. "two sigma", The Citadel college), so
    other false matches are possible, and some real mentions can be missed.
- **Generic headlines can be over-merged.** Duplicate stories are counted once, but two different stories with the
  same generic headline may be treated as one.
- **Respect site terms and `robots.txt`.** Only use feeds and APIs you're licensed to use; the fetcher honors
  `robots.txt` and rate limits, but you're responsible for the sources you add.
- **No personal data beyond public job posts.** The tool tracks firms, not people. Don't add sources that
  collect personal information on individuals, and don't store more than the public evidence snippet.
- This is a prototype template, not a production deployment: the vendor endpoints are placeholders, and the
  rules are tuned on a small fixture set.
