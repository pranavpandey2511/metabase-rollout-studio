# Metabase Computer-Use Rollouts

Local evaluation workspace for running Gemini Computer Use against a seeded,
read-only Metabase gym. It runs on macOS through Colima and stores the complete
evidence for every rollout: screenshots, action trace, submitted answer, logs,
and deterministic grading results.

## What runs where

- **Colima + Docker Compose** run PostgreSQL and Metabase.
- The supplied PostgreSQL archive seeds Metabase before the app starts.
- **Rollout Studio** is the local dashboard at `http://127.0.0.1:8000`.
- A supervised host-to-Colima tunnel exposes the gym at
  `http://127.0.0.1:33000` for Playwright.
- The **orchestrator** starts K attempts, records artifacts, grades outputs, and
  handles cancellation. It is not exposed to Gemini.
- Gemini can interact only with the visible Metabase UI using screenshots,
  mouse, keyboard, scrolling, and waiting. It has no shell, Docker, filesystem,
  direct database, MCP, search, or code-execution tools.

## Prerequisites

- macOS with [Colima](https://github.com/abiosoft/colima) and the Docker CLI.
- Python 3 and a working `python3` command.
- The supplied Metabase archive at `data/metabase_envdata.sql`.
- A Gemini API key and the supplied Metabase login credentials.
- The Gemini `computer-use-preview` checkout at
  `work/computer-use-preview` (it is already present in this workspace).

## One-time setup

From the repository root:

```sh
cp .env.example .env
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r work/computer-use-preview/requirements.txt
.venv/bin/playwright install chromium
```

Edit `.env` and set the two secrets:

```dotenv
GEMINI_API_KEY=your_key
METABASE_PASSWORD=the_supplied_metabase_password
```

Leave these local defaults in place unless you have a specific reason to change
them:

```dotenv
COMPUTER_USE_PYTHON=.venv/bin/python
METABASE_URLS=http://localhost:33000
MAX_PARALLEL_ROLLOUTS=1
MAX_ATTEMPTS_PER_PROBLEM=2
ROLLOUT_TIMEOUT_SECONDS=600
```

`MAX_ATTEMPTS_PER_PROBLEM` is the maximum K the dashboard accepts. The default
is 2 to keep this laptop stable; it can be overridden for a specific launch.

## Start the normal local environment

```sh
./scripts/run_local.sh
```

The script starts or repairs Colima, chooses the `colima` Docker context, starts
PostgreSQL, restores the supplied archive, starts Metabase, waits for
`/api/health`, creates the stable tunnel, and starts Rollout Studio.

Open `http://127.0.0.1:8000` and leave the terminal running while an evaluation
is active. Do **not** start Uvicorn with `--reload`: a reload loses the active,
in-memory job registry while the browser is polling it.

## Fresh experiment with K = 5

A fresh evaluation always gets a new job ID and a new artifact directory. The
gym does not need a database reset between attempts because benchmark tasks are
read-only.

Stop any existing local stack, then launch the dashboard with K=5 enabled:

```sh
./scripts/stop_local.sh
MAX_ATTEMPTS_PER_PROBLEM=5 ./scripts/run_local.sh
```

In the dashboard:

1. Click **New evaluation**.
2. Upload `data/tasks.json`.
3. Select the Gemini Computer Use model.
4. Set **K / attempts per problem** to `5`.
5. Keep parallelism at `1` unless you have intentionally configured a second
   isolated Metabase environment.
6. Submit the evaluation.

With the supplied 10-problem task file, K=5 creates **50 rollouts**. Each
rollout can run for up to 600 seconds, so this can take a long time and consume
Gemini API quota. Start with one problem or K=1 if you are checking setup.

### API alternative

With the dashboard already running, submit the same fresh K=5 evaluation from a
second terminal:

```sh
curl -sS -X POST http://127.0.0.1:8000/api/jobs \
  -F 'tasks_file=@data/tasks.json;type=application/json' \
  -F 'attempts=5' \
  -F 'parallelism=1' \
  -F 'model_name=gemini-3.6-flash' \
  -F 'title=Fresh K5 evaluation'
```

The response contains the job `id`. Monitor it with:

```sh
curl -sS http://127.0.0.1:8000/api/jobs/JOB_ID
```

## Stop commands

- **Stop one evaluation:** open it in the dashboard and click **Stop
  evaluation**. Queued attempts are cancelled and the active Playwright child
  process is terminated; captured artifacts remain available.
- **API equivalent:**

  ```sh
  curl -X POST http://127.0.0.1:8000/api/jobs/JOB_ID/cancel
  ```

- **Stop the complete stack:**

  ```sh
  ./scripts/stop_local.sh
  ```

  This stops Rollout Studio, the SSH tunnel, project containers, and Colima,
  while preserving the seeded Docker volumes.

- **Destructive database reset:** use this only when you explicitly want to
  discard the seeded volume and restore it from the archive on the next start:

  ```sh
  docker compose --profile pool2 down -v --remove-orphans
  colima stop
  ```

## Results and grading

Artifacts are stored under:

```text
runs/<job-id>/<task-id>/<attempt>/
```

Each attempt contains:

- `agent.log` — stdout/stderr, with configured secrets redacted.
- `trace.jsonl` — model turns, thinking, actions, and final event.
- `screenshots/` and `screenshots/manifest.jsonl` — action-linked replay
  frames.
- `final_output.txt` — only the submitted final JSON answer.
- `result.json` — status, selected model, duration, and grading evidence.

Tasks with an `answer` are graded by exact JSON equality. The final structured
JSON value is compared with the golden answer; object-key order is ignored, but
field names, values, list membership, and list order must match. Missing golden
answers are marked `needs_review`; custom grader objects are rejected.

For each problem, the dashboard displays K and the observed `pass^k` value:

```text
passed attempts / K
```

The evaluation-level value is the mean of the per-problem percentages. No
arbitrary threshold turns a problem into a pass/fail verdict.

## Configuration

| Setting | Purpose | Default |
| --- | --- | --- |
| `DEFAULT_COMPUTER_USE_MODEL` | Preselected model in the dashboard | `gemini-3.6-flash` |
| `COMPUTER_USE_MODELS` | Comma-separated model allowlist | Documented Computer Use models |
| `MAX_ATTEMPTS_PER_PROBLEM` | Maximum selectable K | `2` |
| `MAX_PARALLEL_ROLLOUTS` | Maximum simultaneous attempts | `1` |
| `METABASE_URLS` | Comma-separated isolated gym URLs | `http://localhost:33000` |
| `ROLLOUT_TIMEOUT_SECONDS` | Per-attempt timeout | `600` |

The implementation supports a second Compose environment through the `pool2`
profile. It is intentionally not started by the default launcher because this
machine has been more stable with one Metabase instance. Only enable it after
adding a separately tunnelled second URL to `METABASE_URLS`.

## Troubleshooting

| Symptom | What to do |
| --- | --- |
| `Cannot connect to the Docker daemon` | Run `./scripts/run_local.sh`; it starts or repairs Colima and selects its Docker context. |
| `ERR_CONNECTION_REFUSED` at Metabase | Wait for the launcher health check to succeed. If it persists, run `docker compose logs metabase-1`. |
| Dashboard shows an old job as missing | The server restarted; the UI stops polling stale job IDs. Start a new evaluation—saved artifacts remain in `runs/`. |
| K=5 is rejected | Relaunch with `MAX_ATTEMPTS_PER_PROBLEM=5 ./scripts/run_local.sh`, then refresh the dashboard. |
| A run reaches the timeout | Inspect its `agent.log`, trace, and screenshots; it is recorded as an infrastructure error rather than a benchmark pass. |

## Verification

Run the local test suites:

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m unittest discover -s work/computer-use-preview -p 'test_*.py' -v
```

The first suite covers the orchestrator, task parsing, grading, UI payloads,
model validation, and cancellation. The second covers the native Computer Use
tool configuration, blocked actions, and Playwright origin containment.
