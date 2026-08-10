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

## One-time setup

From the repository root:

```sh
cp .env.example .env
./scripts/setup_agent.sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r work/computer-use-preview/requirements.txt
.venv/bin/playwright install chromium
```

The modified Gemini Computer Use source is vendored in
`work/computer-use-preview/`, based on upstream revision
`77c9797e943aad63bbc963b7fd092a9e51c07863`. `setup_agent.sh` only verifies
that source is present; it does not clone, patch, or otherwise modify it. The
local changes remove the example custom function that breaks AFC, record
rollout evidence, recover malformed actions, contain Playwright to the
configured origin, and keep the credential-bearing query out of the process
command line. The query environment value is removed before Chromium starts.
Upstream Browserbase, CLI options, sampling parameters, and documentation stay
intact.

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
MAX_ATTEMPTS_PER_PROBLEM=10
MAX_ROLLOUTS_PER_EVALUATION=100
ROLLOUT_TIMEOUT_SECONDS=600
AUTO_START_ENVIRONMENT=true
```

`MAX_ATTEMPTS_PER_PROBLEM` is the maximum K the dashboard accepts. The default
is 10. Change it in `.env` and restart the dashboard when you need a different
ceiling; both backend validation and the displayed UI maximum use this setting.

## Start the normal local environment

```sh
./scripts/run_local.sh
```

The script starts or repairs Colima without changing the global Docker context,
starts PostgreSQL and Metabase, waits for `/api/health`, creates the stable
tunnel, and starts Rollout Studio. Running it again while Rollout Studio is
already active returns immediately and does not touch Colima or a running job.
The supplied database archive is restored only into a fresh `root_db`; a durable
archive-hash marker makes later repair idempotent and prevents silent overwrite.

Every valid UI submission performs a cheap agent/dependency preflight and the
same environment health check before creating the job. If Colima, Docker, the
Compose services, or the tunnel are down, they are repaired automatically when
`AUTO_START_ENVIRONMENT=true`. Invalid uploads never start infrastructure or
consume Gemini quota.

Rollout Studio itself must be running to receive an upload. Automatic repair
starts Colima, Docker, Compose, Metabase, and the tunnel after the dashboard
receives a valid submission; it cannot start a dashboard process that is down.

Open `http://127.0.0.1:8000` and leave the terminal running while an evaluation
is active. Do **not** start Uvicorn with `--reload`: a reload loses the active,
in-memory job registry while the browser is polling it.

## Fresh experiment with K = 5

A fresh evaluation always gets a new job ID and a new artifact directory. The
gym does not need a database reset between attempts because benchmark tasks are
read-only.

Stop any existing local stack, then launch the dashboard:

```sh
./scripts/stop_local.sh
./scripts/run_local.sh
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

For a single manual rollout without the dashboard, the retained CLI uses the
same preflight, automatic environment startup, runner, artifacts, and grading:

```sh
.venv/bin/python -m app.cli run --tasks data/tasks.json --task-id problem1
```

Each CLI invocation receives a unique evaluation ID, so rerunning the same task
does not overwrite or collide with earlier evidence. Treat it as a manual debug
path: do not run it while a dashboard evaluation is active, and stop it with
Ctrl-C. The dashboard intentionally does not offer a stop button for a process
it does not own.

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
  docker --context colima compose --profile pool2 down -v --remove-orphans
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

### Sharing a sample run

Generated `runs/` artifacts remain ignored by default because their traces,
logs, and screenshots can contain login information. After you manually finish
a successful run, inspect every artifact and screenshot, then explicitly add
only the reviewed evaluation with `git add -f runs/<job-id>`. The dashboard
automatically discovers that checked-in artifact at startup, so an evaluator
can inspect the same evaluation and replay without running Gemini. Do not add
incomplete runs or any run whose screenshots reveal credentials.

Tasks with an `answer` are graded by semantic JSON equality. The last submitted
JSON value is compared with the golden answer; JSON objects are unordered, so
key order is ignored at every depth. JSON types, field names, values, list
membership, and list order must match.
Duplicate keys and non-standard values such as `NaN` are rejected. Missing
golden answers are marked `needs_review`; custom grader objects are rejected.

For each problem, the dashboard displays K and the observed `pass^k` value:

```text
passed attempts / K
```

The evaluation-level value is the mean of the per-problem percentages. No
arbitrary threshold turns a problem into a pass/fail verdict. A model timeout or
missing final answer is a failed attempt and therefore lowers passed/K, matching
the assignment's treatment of stuck rollouts. API, browser-process, Docker, and
harness failures are infrastructure errors; an evaluation containing those
errors is invalid until the affected attempts are rerun.

## Configuration

| Setting | Purpose | Default |
| --- | --- | --- |
| `DEFAULT_COMPUTER_USE_MODEL` | Preselected model in the dashboard | `gemini-3.6-flash` |
| `COMPUTER_USE_MODELS` | Comma-separated model allowlist | Documented Computer Use models |
| `MAX_ATTEMPTS_PER_PROBLEM` | Maximum selectable K | `10` |
| `MAX_ROLLOUTS_PER_EVALUATION` | Safety ceiling for problems × K | `100` |
| `MAX_PARALLEL_ROLLOUTS` | Maximum simultaneous attempts | `1` |
| `METABASE_URLS` | Comma-separated isolated gym URLs | `http://localhost:33000` |
| `ROLLOUT_TIMEOUT_SECONDS` | Per-attempt timeout | `600` |
| `AUTO_START_ENVIRONMENT` | Repair local Colima/Compose on submission | `true` |
| `ENVIRONMENT_START_TIMEOUT_SECONDS` | Overall startup deadline | `180` |
| `ENVIRONMENT_HEALTH_WAIT_SECONDS` | Metabase readiness deadline | `120` |
| `TUNNEL_REPAIR_WAIT_SECONDS` | Tunnel-only repair window before Compose repair | `5` |
| `SHUTDOWN_GRACE_SECONDS` | Time allowed for active agents to cancel | `15` |
| `MAX_TASK_FILE_BYTES` | Uploaded task-file limit | `2000000` |
| `COLIMA_CPU`, `COLIMA_MEMORY_GB`, `COLIMA_DISK_GB` | Colima resources | `4`, `4`, `60` |

The implementation supports a second isolated Compose environment through the
`pool2` profile. To use it, set `MAX_PARALLEL_ROLLOUTS=2` and
`METABASE_URLS=http://localhost:33000,http://localhost:33001`. The runtime then
starts both slots automatically. Parallelism above the configured capacity is
rejected rather than silently capped.

## Assignment coverage

- **Gym:** pinned Metabase + PostgreSQL 17, restored from the supplied archive.
- **Agent:** Gemini Computer Use sees one native `computer_use` declaration and
  is limited to the configured Metabase origin. It receives no shell, SQL,
  database, MCP, search, code-execution, Docker, or host-filesystem tool.
- **Orchestration:** task JSON ingestion, configurable K/model/parallelism,
  isolated environment slots, bounded workers, timeouts, cancellation, and one
  active dashboard evaluation to prevent slot sharing. The separate manual CLI
  is documented as non-concurrent.
- **Observability:** per-attempt screenshots, action-linked manifest, model
  trace, final answer, redacted log, result, and grading evidence.
- **UI:** job submission and stopping, live persisted progress, K and pass^k at
  every level, attempt replay, and real browser-history routes.

The supplied assignment treats Metabase as read-only. This MVP enforces the
agent/tool/origin boundary and instructs the model not to write; it does not
pretend that browser method blocking is a database permission system. If write
tasks are introduced, each rollout should receive a disposable database
snapshot and a least-privilege account, followed by teardown or rollback.

For thousands of rollouts, replace the in-process executor and JSON metadata
with a durable queue, worker leases, a relational job store, and object storage
for artifacts. Add idempotent attempt IDs, retries only for classified
infrastructure failures, quotas/backpressure, and per-rollout disposable gyms.
Those are production extensions, not necessary complexity for this local
assignment.

## Troubleshooting

| Symptom | What to do |
| --- | --- |
| `Cannot connect to the Docker daemon` | Run `./scripts/run_local.sh`; it starts or repairs Colima and addresses the `colima` context explicitly. |
| `ERR_CONNECTION_REFUSED` at Metabase | Let automatic repair finish. If it persists, run `docker --context colima compose logs metabase-1`. |
| A previous job says it was interrupted | Rollout Studio reconciles unfinished persisted jobs after a server restart. Inspect the saved evidence, then start a new evaluation. |
| A K value is rejected | Set `MAX_ATTEMPTS_PER_PROBLEM` to at least that value in `.env`, restart with `./scripts/run_local.sh`, then refresh the dashboard. |
| The total rollout count is rejected | Raise `MAX_ROLLOUTS_PER_EVALUATION` deliberately; the default allows the supplied 10 problems at K=10. |
| AFC reports incompatible tools | Run `./scripts/setup_agent.sh`; preflight refuses an incomplete vendored adapter. |
| A run reaches the timeout | Inspect its `agent.log`, trace, and screenshots; it is recorded as a failed attempt and lowers passed/K. |

## Verification

Run the local test suites:

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m unittest discover -s work/computer-use-preview -p 'test_*.py' -v
```

The first suite covers the orchestrator, task parsing, grading, UI payloads,
model validation, and cancellation. The second covers the native Computer Use
tool configuration, blocked actions, and Playwright origin containment.

Final validation on 2026-08-10: 68 project tests and 22 adapter tests passed;
shell syntax, Python compilation, and source checks passed. A
cold-start dashboard submission with Colima stopped automatically restored the
Docker path, reached healthy Metabase, ran Gemini, captured 8 aligned frames,
and passed exact JSON grading in 65.25 seconds. Its temporary artifacts were
removed after verification so the handoff starts with an empty run history.
