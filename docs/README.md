# Documentation

Start with the [project README](../README.md) ([English](../README.en.md)): what ISBE does, a visual tour
and a short quick start. The pages below hold the rest.

| Document | Read it to |
| --- | --- |
| [features.md](features.md) | see what is supported and what is not yet (Chinese) |
| [getting-started.md](getting-started.md) | set up from source, generate the first paper digest, understand the task queue and the "no papers" message (Chinese) |
| [configuration.md](configuration.md) | configure the model service, environment variables, mail delivery, full-text extraction, keys and shared mode (Chinese) |
| [topics.md](topics.md) | manage paper topics, schedule them externally, and find the sources and material inbox (Chinese) |
| [research.md](research.md) | run fixed-material research and publish edited media briefings (Chinese) |
| [commands.md](commands.md) | look up every `isbe` command (Chinese) |
| [deployment.md](deployment.md) | run the Docker image, upgrade, and back up the data directory (Chinese) |
| [development.md](development.md) | run the tests and the frontend dev server, and contribute (Chinese) |
| [DESIGN.md](DESIGN.md) | understand the stages (collect, triage, enrich, digest, memory, web console, shared mode), the data model and what is planned |
| [architecture.md](architecture.md) | find your way around the code: package layout, dependency rule, compatibility contract, conventions and tests |
| [source-interface.md](source-interface.md) | configure RSS / website sources, web search plans, date checks and the material inbox (Chinese) |
| [contracts/question/](contracts/question/README.md) | see the JSON Schemas, sample questions and metrics shared by the question → paper set → knowledge graph feature |
| [settings.md](settings.md) | set up a new install from one JSON file: the import/export format, the Settings page buttons, `isbe settings import/export` and how keys are handled |
| [process.md](process.md) | open an issue or merge request: the real-run evidence each one carries, the templates, and the separate review before merge |
| [standards/](standards/README.md) | see what frontend and backend changes are held to, when a task is done, and how review findings converge and an issue closes |
| [testing.md](testing.md) | run the tests, see the test layers, and replay or re-record the essaies and model data of the sample questions |
| [upgrading-from-rust.md](upgrading-from-rust.md) | move a data directory from the former Rust build, and see what changed |
| [../deploy/searxng/README.md](../deploy/searxng/README.md) | run a local SearXNG for web search (Chinese) |
| [question/planning.md](question/planning.md) | see how a question becomes its search plan, how plan versions work, and how to use a local model (Strata) for it |
| [question/search.md](question/search.md) | see how the plan's queries run against essaies, how papers are deduplicated, and how paper sets and their operations are stored and replayed |
| [question/screening.md](question/screening.md) | see how the model screens every candidate against the plan's criteria, how failed batches stay unscreened, how overrides work, and what screening measured on Strata |
| [question/survey.md](question/survey.md) | write a survey report from a run's included papers with `isbe question-survey`: which papers, full text and verified facts, how citations are checked, where the report goes and its limits |
| [question/api.md](question/api.md) | drive a question run from the console or `isbe question`: the `/api/question/*` routes, the server-sent event stream, pause, resume, cancel, rerun and the queue |

Examples live in [`../examples/`](../examples): topic files, source and search
plans, fixed-material research manifests and a settings file to import. [`.env.example`](../.env.example)
lists the environment variables.

README.md and the pages marked (Chinese) are written in Chinese; the other
pages, and the English README ([README.en.md](../README.en.md)), are in English.
