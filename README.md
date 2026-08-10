# Metabase Computer-Use Rollouts

Run and evaluate Gemini Computer Use against a seeded, read-only Metabase
environment. Every rollout retains its screenshots, action trace, final answer,
logs, and grading evidence so a result can be reviewed after the run finishes.

## How it works

1. Docker starts PostgreSQL and Metabase from the supplied database archive.
2. Rollout Studio accepts a task file and starts K attempts for each task.
3. Gemini works through the visible Metabase UI only; it does not receive shell,
   database, filesystem, or code-execution access.
4. The runner saves evidence for every attempt and grades the final JSON answer
   against the task's golden answer.

## Prerequisites

- macOS, Docker Desktop or Colima, and the Docker CLI.
- Python 3.10 or later. The project virtual environment is the supported
  runtime.
- `data/metabase_envdata.sql`, which is supplied with this workspace.
- A Gemini API key and the supplied Metabase password.

## Setup

From the repository root:

```sh
cp .env.example .env
./scripts/setup_agent.sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r work/computer-use-preview/requirements.txt
.venv/bin/playwright install chromium
```

Set these values in `.env`:

```dotenv
GEMINI_API_KEY=your_key
METABASE_PASSWORD=the_supplied_metabase_password
```

The default configuration uses one local Metabase environment and one rollout
at a time. For normal use, the defaults are sufficient.

## Run an evaluation

Start the local environment and dashboard:

```sh
./scripts/run_local.sh
```

Open [Rollout Studio](http://127.0.0.1:8000), then:

1. Select **New evaluation**.
2. Upload `data/tasks.json`.
3. Select the model and choose K (attempts per problem).
4. Keep parallelism at `1` unless a second isolated environment is configured.
5. Submit the evaluation and leave the terminal running.

The supplied task file has 10 tasks. K=2 creates 20 rollouts; K=5 creates 50.
Each rollout can run for up to 600 seconds, so begin with K=1 when checking a
new setup. The dashboard shows live progress, pass/K for each task, and a
replay view after completion.

For a single manual debugging rollout:

```sh
.venv/bin/python -m app.cli run --tasks data/tasks.json --task-id problem1
```

Do not run this CLI command while a dashboard evaluation is active.

## Stop an evaluation

Use **Stop evaluation** in Rollout Studio to stop one job and preserve the
evidence collected so far. To stop the dashboard and project containers:

```sh
./scripts/stop_local.sh
```

The seeded Docker volumes are preserved. A stopped or interrupted attempt is
not evidence of task failure; inspect its recorded error before rerunning it.

## Results and sample run

Each attempt is stored at:

```text
runs/<job-id>/<task-id>/<attempt>/
```

The directory contains `result.json` (status and grade), `final_output.txt`,
`agent.log`, `trace.jsonl`, and action-linked screenshots. Logs and traces
redact configured credentials; screenshots still require review before sharing.

The checked-in sample evaluation, [`41fbdf7da86a`](runs/41fbdf7da86a/), was
completed on 2026-08-10 using `gemini-3.6-flash`, K=2, and parallelism 1. It
contains 20 completed attempts: 14 passed and 6 failed, for an evaluation score
of 70%.

- [Job metadata and all grades](runs/41fbdf7da86a/job.json)
- [A passing attempt: problem 1, attempt 1](runs/41fbdf7da86a/problem1/1/result.json)
- [The list-order failure: problem 9, attempt 1](runs/41fbdf7da86a/problem9/1/result.json)
- [The submitted output for that failure](runs/41fbdf7da86a/problem9/1/final_output.txt)

`runs/` is ignored by default because evidence can contain sensitive material.
Only reviewed artifacts should be published. To include a reviewed evaluation
in a commit, explicitly add it with `git add -f runs/<job-id>`.

## Evaluation and its limitation

The grader extracts the last valid JSON object or array in the final answer and
compares it with the task's golden answer. JSON object key order is ignored, but
types, fields, values, and array positions must match exactly. The dashboard
reports each problem as `passed attempts / K`; the evaluation score is the mean
of those per-problem rates.

This is reliable when the prompt specifies order, such as “top three products
by order count.” It is too strict when a prompt asks for an unordered
collection without defining a sort order. For example, `problem9` asks for all
matching names but does not say how to order them. Its sample result contains
the correct four names in reverse order and therefore fails the current exact
array comparison. [See the expected and observed result.](runs/41fbdf7da86a/problem9/1/result.json)

Until the task format supports order semantics, task authors should state a
deterministic ordering, for example “sort names alphabetically.” The planned
fix is a per-task grading declaration such as an `unordered_array_paths` field.
The default will remain ordered arrays; paths explicitly marked unordered will
compare values and multiplicities without comparing positions. That keeps
ranked results strict while accepting equivalent set-like results. This schema
and comparison behavior are not implemented yet.

## Configuration

The most useful optional `.env` settings are:

| Setting | Purpose | Default |
| --- | --- | --- |
| `DEFAULT_COMPUTER_USE_MODEL` | Model selected in the dashboard | `gemini-3.6-flash` |
| `MAX_ATTEMPTS_PER_PROBLEM` | Largest selectable K | `10` |
| `MAX_PARALLEL_ROLLOUTS` | Concurrent attempts | `1` |
| `ROLLOUT_TIMEOUT_SECONDS` | Per-attempt timeout | `600` |
| `DOCKER_BACKEND` | `auto`, `docker`, or `colima` | `auto` |

Restart Rollout Studio after changing configuration.

## Assignment coverage

- **Gym:** Metabase and PostgreSQL 17 restored from the supplied archive.
- **Agent boundary:** Gemini is confined to the configured Metabase origin and
  receives only Computer Use actions.
- **Orchestration:** task ingestion, configurable model/K/parallelism, bounded
  workers, timeouts, cancellation, and isolated local environment slots.
- **Evidence:** screenshots, action trace, final answer, redacted log, result,
  and grading checks for each attempt.
- **Review:** live job progress and replayable attempt details in Rollout
  Studio.

The benchmark is read-only. If write tasks are added, each rollout needs a
disposable database snapshot and least-privilege credentials before execution.

## Troubleshooting

| Symptom | What to do |
| --- | --- |
| `Cannot connect to the Docker daemon` | Start Docker Desktop, or install Colima and run `./scripts/run_local.sh`. |
| Metabase is unavailable | Wait for startup to complete, then inspect `docker compose logs metabase-1`. |
| Python errors around `zip(..., strict=True)` | Recreate `.venv` with Python 3.10 or later and rerun setup. |
| A job was interrupted | Inspect its saved artifacts, then start a new evaluation; do not score the interruption as a task failure. |
| A rollout timed out | Inspect its log, trace, and screenshots. A task timeout lowers pass/K; an infrastructure error invalidates the affected evaluation until rerun. |

## Verification

Run the project and vendored Computer Use tests with the project environment:

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m unittest discover -s work/computer-use-preview -p 'test_*.py' -v
```

Also verify the Compose configuration before a fresh environment run:

```sh
docker compose config --quiet
```
