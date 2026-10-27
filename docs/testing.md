# Testing

Every offline test runs with one command and needs no essaies, Strata or other
network service:

```bash
scripts/py-test
node --test web/*.test.cjs tests/*.test.cjs
```

`scripts/py-test` runs pytest in the pinned Python container with a coverage
summary, then `scripts/web-test` (the node tests, ESLint, Prettier, Vitest with
a coverage summary, `tsc` and the build); the front end runs even when pytest
fails, and the script fails if either did. Outside Docker, `uv run pytest` runs
the same suite. Tests marked `live` are skipped unless selected (see
[Live tests](#live-tests)).

## CI

[`.gitlab-ci.yml`](../.gitlab-ci.yml) runs the same checks on every merge
request and on `main` (branches without a merge request too, except
`evidence`). Each check is its own job, so one failing never hides another:

| Job | Runs | Report |
| --- | --- | --- |
| `py-lint` | `ruff check` | log |
| `py-test` | `pytest --cov` | JUnit tests tab, Cobertura coverage in the diff, coverage % |
| `web-classic` | `node --test web/*.test.cjs tests/*.test.cjs` | log |
| `web-lint` | ESLint and Prettier (`frontend/`, `scripts/*.mjs`) | log |
| `web-test` | `vitest run --coverage` | JUnit, Cobertura, coverage %, HTML report artifact |
| `web-build` | `tsc --noEmit` and `vite build` | the built console as an artifact |
| `docker-image` | the [Dockerfile](../Dockerfile), built with kaniko and pushed to the project registry as `ci:<short sha>` | log |
| `docker-smoke` | `isbe --version`, the server import and the bundled console, inside that image | log |

The jobs use the images the scripts use (`python:3.14-slim` with uv,
`node:24-slim`) on a clean checkout, so a failure in CI almost always shows up
under `scripts/py-test` too (CI runs as root, and `scripts/web-test` uses a
local Node 22+ when there is one). A merge request pipeline tests the branch
head, not its merge with `main`, which is why branches are rebased first.

The runner host reaches PyPI and npm only through mirrors (Tsinghua for PyPI,
Tencent for npm), so the jobs install from them; the `PIP_INDEX_URL`,
`UV_DEFAULT_INDEX`, `PYPI_FILES_MIRROR` and `npm_config_registry` job variables
choose them, and a project CI/CD variable of the same name overrides them. Its
Docker Hub access is also missing, so the runner uses the `if-not-present`
pull policy with the job images loaded on the host, and `docker-image` takes
its base images from a mirror (`DOCKER_BASE`). The runner shares no Docker
daemon with its jobs, which is why the image is built with kaniko
(`gcr.io/kaniko-project/executor:v1.23.2-debug`, loaded on the host like the
other job images; upstream kaniko is archived, and `cgr.dev/chainguard/kaniko`
is the maintained fork); the
tests do not run in the image build, they are the jobs above. A merge request's pipeline for its head commit
must be green before it merges ([done.md](standards/done.md#3-green-on-current-main)).

## Layers

| Layer | What it checks | Where | Runs |
| --- | --- | --- | --- |
| Unit | One module's behavior, with `httpx.MockTransport` or a local `http.server` thread for HTTP | `tests/py/<area>/` | offline |
| Contract | The question-research JSON Schemas, their examples and the sample questions; any document a test produces can be checked with `question_schemas.errors` | `tests/py/contracts/`, `tests/py/question_schemas.py` | offline |
| Recorded data | essaies responses recorded for the sample questions, replayed through ISBE's HTTP client; leak scan of every fixture file | `tests/py/testkit/`, `tests/fixtures/question/` | offline |
| Model | Pipeline stages against `FakeModel`: fixed replies, plus broken JSON, timeouts, Strata's context-window 400, cut-off replies, refusals and server errors | `tests/py/testkit/test_fake_model.py`, stage tests | offline |
| Frontend | React components and pages against the recorded data (Vitest) | `frontend/` | offline; arrives with the React shell |
| Browser | Ask → search → override → 2D/3D → export in Chromium on recorded data (Playwright) | `frontend/` | offline; arrives with the React shell |
| Regression | Weekly reports, queue restart recovery, shared accounts, mail | existing suites under `tests/py/` | offline |
| Live | Real essaies and Strata: drift of the recordings, acceptance runs and their metrics | tests marked `live` | on demand |

## Recorded data

[`tests/fixtures/question/`](../tests/fixtures/question/README.md) holds, per
sample question, its search plan, the essaies responses a run asks for and the
model's replies. `tests/py/replay.py` replays them:

```python
import replay

s = replay.sample("radar-nowcasting-dl")
client = http.Http(5, transport=s.essaies.transport())  # recorded essaies
model = Llm.from_vars(env.get, client=http.Http(5, transport=s.model().transport()))
```

A request without a recording fails the test with `replay.Unrecorded` instead
of reaching the network. Frontend tests read the same JSON files.

### The fake model

`replay.FakeModel` is an OpenAI-compatible chat endpoint. A reply is chosen by
the SHA-256 of the system and user text (`replay.prompt_sha`) or by text the
prompt contains; faults are injected per call:

```python
fake = replay.FakeModel().reply(plan_json, contains=question).fault("bad_json")
# the first call gets broken JSON, the next the plan
```

Faults: `bad_json`, `timeout`, `context` (Strata's 400, which `Llm` fits and
retries once), `cut`, `refuse`, `server_error` (retried by `Llm`); `times=None`
fails every matching call.

### Re-recording

```bash
ISBE_LIVE_ESSAIES_URL=http://<essaies> uv run python tests/py/replay.py [--missing] [sample-id ...]
```

It records the searches of `plan.json` and of the plan Strata's recorded reply
(`model.strata.json`) gives. `--missing` keeps what is recorded and adds only
the requests the cassette lacks or recorded as a failure.

The recorder drops essaies' own fields (archive paths) and refuses any body
that still contains a private address, `localhost`, a local path or a
key-like string. `tests/py/testkit` checks every fixture file the same way, and
pins how many must-find papers the recorded searches return; a change there
after re-recording is a change in essaies or OpenAlex to look at before
updating the numbers.

Real model replies are recorded with `replay.ModelRecorder` around the
transport of a configured `Llm`; it keeps each reply under its prompt hash and
`save` merges it into a sample's `model.json`.

## Live tests

```bash
ISBE_LIVE_ESSAIES_URL=http://<essaies> uv run pytest -m live
```

Live tests skip themselves when the service they need is not configured. Never
commit service addresses: they stay in the environment.
