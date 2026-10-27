"""Question research: a research question → a paper set → the default knowledge graph.

The data shapes are the contracts in docs/contracts/question/. So far:

- `plan`: the question → an editable, versioned search plan (`question_plan@1`).
- `runs`: question runs and their event stream (`question_event@1`).
- `sources`: essaies' OpenAlex mirror; identity keys and the paper record.
- `sets`: paper sets (`paper_set@1`) and their recorded, replayable operations (`set_op@1`).
- `search`: the plan's queries in priority order → the candidate set.
- `screen`: the model's verdict on every candidate, and people's overrides.
- `queue`, `flow`: the queue of runs and the steps the worker takes (plan, review,
  search, screen), with pause, resume and cancel.
- `service`: asking, rerunning and reading runs, for the API and the CLI.
"""
