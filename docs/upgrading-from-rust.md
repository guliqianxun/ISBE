# Upgrading from the Rust build

ISBE was first written in Rust and moved to Python 3.14 on 2026-10-20. The
Python package is the same product: it opens the same data directory, serves
the same console and takes the same commands. The Rust sources, their
Dockerfile and the comparison harness were removed afterwards; they remain in
git history up to commit `7d0138d`, which is the one to check out if you need
to build the Rust binary again.

## What carries over

Everything listed in the [compatibility contract](architecture.md#compatibility-contract-do-not-break):
the data directory layout, the database and its migrations, `settings.json`,
topic files, accounts and sessions, the HTTP API, the CLI and existing
reports. No export or conversion step is needed.

## Switching a data directory

1. Stop the Rust service. Never run two instances on one data directory.
2. Back up the whole data directory. The Python build applies newer
   migrations, and an older build cannot open the upgraded database.
3. Build the image and start it on the same data directory, as in the
   [Docker guide](deployment.md#docker):

   ```bash
   docker build -t isbe .
   docker run --rm isbe --version
   ```

4. Check that the image holds the Python package, not a stale build:
   `--version` prints `isbe 0.2.0.dev0` or later, and this prints `ok`:

   ```bash
   docker run --rm --entrypoint python isbe -c "import isbe.web.server; print('ok')"
   ```

5. Start the service with your usual `serve` options and open the console.

## Intended differences

Each row is a fix for a problem found while reviewing the Rust build, or a
side effect of one, and has a test. Read them before switching.

| Area | Difference |
| --- | --- |
| Reports | Remote, `data:` and other non-figure images are not rendered. Untrusted fields are Markdown-escaped. Responses carry a CSP (`script-src 'self'`, `frame-ancestors 'none'`), `nosniff` and `no-referrer`. |
| Console requests | Bodies have size and read-time limits (408/413), and the number of connections is capped. |
| Login | Throttling is per source address and per account, not global. Behind a proxy, set `ISBE_TRUSTED_PROXIES` (addresses or CIDRs) so the client address is used. If the Rust build later runs on the same database, its cleanup drops the "trusted source" rows, so that trust is lost but nothing breaks. |
| Evidence cards | Numbers, URLs and quotes are checked against the fact's own quote. Cards cached by Rust keep their old verdicts; `DELETE FROM cards` re-checks them. |
| Memory drafts | Frontmatter values are quoted. A draft Rust already wrote for a period gets one `-2` copy the first time Python re-runs that period. |
| Report queue | Only generation settings are fingerprinted, so mail changes no longer fail queued tasks. Rust-queued tasks run if nothing Rust would have flagged has changed. |
| Feeds | GBK/GB2312 and other declared encodings are read. |
| Mail | Report and test emails are rate-limited per account: 10/hour and 30/day for members, 60/hour and 300/day for admins. Counters are in memory. TLS handshake failures report `failed`, not `unknown`; nothing is sent in that case. |
| Pipeline | A refused or filtered model reply fails the triage, digest or review step instead of passing silently. A digest refuses a period whose file another report kind owns. A reviewer rewrite that drops papers is not adopted. |
| Topics | One broken topic file no longer breaks the topic list, overview or triage view. Member topics cap full-text budgets at 8000 characters per paper and 48000 in total. |
| Article dates | Dates are compared as instants across time zones, so early-morning Beijing publications are no longer conflicts. HTML is parsed as a tree. |
| Source discovery | Re-running discovery keeps a source the user edited, instead of failing on its key. A run's new sources are written in one transaction when the job finishes. |
| JSON numbers | Output matches serde_json, including `1e+20`. On input, a literal beyond 2^53 that falls exactly halfway between two doubles (e.g. `9007199254740993.0`) is rounded correctly, where serde's default parser rounds it up. |
| Messages | YAML errors omit Rust's source snippet. JSON errors omit serde's line and column. Network errors use httpx wording. The User-Agent is `isbe/<version>`. |

## Going back

Restore the backup from step 2 and start the Rust build from the commit
above. A database that Python has already migrated cannot be opened by the
Rust build.
