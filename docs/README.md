# Documentation

Start with the [project README](../README.md) ([English](../README.en.md)):
what ISBE does, the supported feature list and a quick start.

| Document | Read it to |
| --- | --- |
| [configuration.md](configuration.md) | look up the full reference: service settings, mail, full text, keys and shared mode, topics, sources, fixed-material research, commands, Docker, media briefings, data and backup, development (Chinese) |
| [DESIGN.md](DESIGN.md) | understand the stages (collect, triage, enrich, digest, memory, web console, shared mode), the data model and what is planned |
| [architecture.md](architecture.md) | find your way around the code: package layout, dependency rule, compatibility contract, conventions and tests |
| [source-interface.md](source-interface.md) | configure RSS / website sources, web search plans, date checks and the material inbox (Chinese) |
| [upgrading-from-rust.md](upgrading-from-rust.md) | move a data directory from the former Rust build, and see what changed |
| [../deploy/searxng/README.md](../deploy/searxng/README.md) | run a local SearXNG for web search (Chinese) |

Examples live in [`../examples/`](../examples): topic files, source and search
plans, and fixed-material research manifests. [`.env.example`](../.env.example)
lists the environment variables.

The README has a Chinese and an English version. `configuration.md` and
`source-interface.md` are written in Chinese; the other pages are in English.
