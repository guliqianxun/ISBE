# Settings file (import and export)

A new install is configured from one JSON file instead of typing every field
on Settings (#114). The console and the CLI read and write the same format.

```json
{
  "format": "isbe-settings",
  "version": 1,
  "settings": {
    "ISBE_LLM_PROVIDER": "openai",
    "ISBE_LLM_BASE_URL": "http://127.0.0.1:8080/v1",
    "ISBE_LLM_TIMEOUT_S": 900,
    "ISBE_LLM_EXTRA_BODY": { "chat_template_kwargs": { "enable_thinking": false } },
    "ISBE_PAPER_SOURCE": "openalex",
    "OPENALEX_MAILTO": "you@example.org",
    "SEARXNG_URL": null
  }
}
```

[examples/settings.example.json](../examples/settings.example.json) is a
starting point; both test suites read it, so the two readers agree. It has no
API key: add `"OPENAI_API_KEY": "<your key>"` (or the key of your provider)
and `"OPENALEX_API_KEY": "<your key>"` before importing, or type the keys on
the page afterwards. To search through essaies instead of the OpenAlex API, set
`"ISBE_PAPER_SOURCE": "essaies"` and `"ESSAIES_URL"`.

- **Names** are the setting names of `isbe/core/settings.py` `KEYS`: the same
  names as the environment variables and `<data>/settings.json` (the table in
  [configuration.md](configuration.md)). An object of names with no `format`
  (a copied `settings.json`, or an env file turned into JSON) is read too.
- **Values** are strings. A whole number or boolean is read as its text
  (`900` and `900.0` → `"900"`; `1.5` is refused, every numeric setting is a
  whole number); `ISBE_LLM_EXTRA_BODY` may be an object, written back as
  compact JSON with sorted keys. `null` or `""` clears a
  saved value (a field back to its default, a saved secret removed).
- **Unknown names** are listed and skipped. Settings of removed features
  (`BAIDU_SEARCH_API_KEY`, `ISBE_SEARCH_DAILY_LIMIT`) are skipped quietly.
- **Limit**: 64 KB, like `settings.json`. A file that is not JSON, not an
  object, has another `format` or `version`, or a value of another type is
  refused as a whole.

Every value is then checked the way a Save checks it (URLs, numbers, the
provider); one invalid value saves nothing.

## On the Settings page

**Import JSON…** fills the form in, as if the values had been typed; nothing is
saved until **Save settings**, and **Discard changes** undoes the import. The
note names every setting the file changed, including ones without a field on
the page right now (the key of a provider not chosen), and those already the
same. Names
the server's environment sets are locked on the page, so they are not imported
and the note says which. Imported advanced model options open that section.

**Export JSON** downloads `isbe-settings.json`: the settings in effect (saved
values, and environment values on top), defaults left out. Keys, tokens and
passwords never reach the page, so they are not in the file; `secrets_omitted`
names the ones that are set, to add by hand. Export waits until unsaved
changes are saved or discarded. Because environment values are included,
importing such a file elsewhere saves them into that install's
`settings.json`, where they keep applying after the variable is gone.

## From the command line

```bash
isbe --data DIR settings import isbe-settings.json
isbe --data DIR settings export > isbe-settings.json
isbe --data DIR settings export --secrets > isbe-settings.json   # includes keys
```

`import` prints one line per name (`set`, `cleared`, `skipped`; never a value)
and marks names the environment overrides, which keep winning while that
variable is set. It works before any account exists, so a new server can be
configured before `serve`. `export` includes secrets only with `--secrets`; an environment value that is
not valid is left out with a warning on stderr (the saved value, if any, is
exported instead).

A file with keys in it is as sensitive as `settings.json` (mode 0600 on the
server): do not commit it or send it around.
