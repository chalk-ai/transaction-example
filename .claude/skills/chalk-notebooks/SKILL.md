---
name: chalk-notebooks
description: Use when creating, running, or debugging a Chalk notebook — via `chalk notebook create`/`cell add`/`cell edit`/`run`, or working inside a notebook opened in the dashboard. Covers running online or offline queries in a cell, iterating on feature definitions live with `client.load_features()`, plotting with Altair, installing packages with `uv`, and training a model (e.g. XGBoost) inside the notebook kernel.
---

# Chalk Notebooks

## Overview

Chalk notebooks (created via `chalk notebook create` and driven with `chalk notebook cell add/edit/run`, or opened directly in the dashboard) are the fastest way to explore features and iterate on queries. See `chalk notebook --help` for the full command reference (create, cell add/edit/move/delete, run, results, get, output rows/download, kernel status).

The notebook kernel is a separate, hosted Python environment — it is **not** your local machine or your project's checked-out repo. Most of the gotchas below come from that fact: it has its own package set, doesn't have your project's code on its path, and needs to be driven through the `chalk` CLI (or dashboard) rather than direct file access.

## When to Use

- Creating a new notebook to explore features or test a query
- Running an online or offline query inside a notebook cell
- Iterating on a new feature definition before committing it to the codebase
- Plotting results inside a notebook
- Installing a package inside a notebook kernel
- Training a quick model (e.g. XGBoost) inside a notebook

## Open the notebook in the user's browser as soon as you create it

`chalk notebook create` prints the notebook's dashboard URL. Open it immediately (e.g. `open <url>` on macOS, `xdg-open <url>` on Linux) so the user can watch cells run live in the dashboard while you drive the notebook from the CLI, instead of only seeing your terminal output after the fact.

## Return query results directly - don't call `.to_dict()`, `.to_df()`, or `print()` on them

This is the single most important notebook habit: when a cell's result is an online or offline query response, make the query object itself the cell's last expression. Don't convert it, print it, or otherwise post-process it first.

```python
# CORRECT - the bare response is the last expression
result = client.query(
    input={"user.id": 1},
    output=["user.id", "user.name", "user.fraud_score"],
)
result

# WRONG - throws away the rich rendering
result = client.query(input={"user.id": 1}, output=[...])
result.to_dict()          # degrades to a plain dict dump
print(result)              # degrades to a plain-text repr
```

Both the dashboard and the CLI (`chalk notebook cell add --run`, etc.) recognize the raw response object and render it as a proper feature/value table - a single row transposes into a readable table, a bulk query renders as a normal table, and has-many features get their own sub-tables. Calling `.to_dict()`/`.to_df()`/`print()` yourself replaces that with a worse, harder-to-read dump, and if you round-trip through Python data structures the renderer has no way to recover the original shape.

The same applies to offline queries - return the `Dataset`/`DatasetRevision` object, or the realized dataframe, directly:

```python
# CORRECT
dataset = client.offline_query(
    input={"user.id": list(range(1000))},
    output=["user.id", "user.fraud_score"],
    dataset_name="fraud_training_data",
    recompute_features=True,
)
dataset          # shows dataset/revision metadata, and polls until it's ready

# CORRECT - once you want actual rows
df = dataset.to_pandas()
df                # shows a preview table + per-column statistics

# WRONG
df = dataset.to_pandas()
print(df.head())   # throws away the preview + statistics rendering
```

Note that `client.offline_query(...)` returns as soon as the revision is created - it does not wait for the query to finish. Returning the bare `Dataset` object as a cell's last expression is what triggers polling until the revision completes and then shows the preview and statistics tables; there's no need to write your own polling loop or status prints.

## Notebooks don't have your project's package on their path - use string FQNs or `client.load_features()`

A notebook created with `chalk notebook create` (or opened in the dashboard) runs in a hosted kernel that only has the `chalk` client installed - it does **not** have your project's own Python package (e.g. `src/`) on its path, so `from src.models import User` fails with `ModuleNotFoundError`. There are two correct ways to work around this, and no need to reach for anything more elaborate:

```python
# Option 1 - reference features by string FQN, no feature classes needed
from chalk.client import ChalkClient

client = ChalkClient()
result = client.query(input={"user.id": 1}, output=["user.id", "user.fraud_score"])
result

# Option 2 - load the deployed feature classes into the session
client.load_features()   # binds User, Transaction, CreditReport, etc. as globals
result = client.query(input={User.id: 1}, output=[User.id, User.fraud_score])
result
```

`client.load_features()` pulls the feature classes from the deployed environment and binds them into the notebook's namespace, so `User`, `Transaction`, and every other `@features` class become usable exactly as if you'd imported them - without needing your project's package on the kernel's path. This is also what unlocks live feature iteration (below): you need real feature classes, not string FQNs, to attach a new expression feature to one.

If you're instead running a local Jupyter kernel from within your project's checked-out repo, your feature classes are importable as usual and any of these styles work.

## Iterate on feature definitions live in a notebook before committing them

After `client.load_features()`, you can attach a brand-new expression feature directly to a loaded class and test it with a query in the same session - a much faster loop than editing your resolvers file, redeploying, and re-querying for every small change:

```python
client.load_features()

import chalk.functions as F
from chalk import _

User.ach_return_amount: float = _.transactions[_.status == "RETURNED", _.amount].sum()
User.name_email_levenshtein: int = F.levenshtein_distance(F.lower(_.name), F.lower(_.email))

# Test it immediately
client.query(
    input={User.id: 1},
    output=[User.name, User.email, User.ach_return_amount, User.name_email_levenshtein],
)
```

Once a feature is working the way you want, move its definition into your actual feature class in the codebase and deploy normally (`chalk apply`) - the notebook version is for iteration, not a substitute for committing the feature.

If you use a feature you just defined live as output of an `offline_query`, pass `recompute_features=True`. Without it, the query samples already-computed values from the online/offline store rather than running resolvers fresh - and a feature you just defined live has no such stored history. Usually it's just better to recompute.

## Run exactly one offline query per cell - never in a loop

An offline query is a heavyweight, asynchronous job: it creates a dataset revision, runs resolvers across every row, and takes seconds to minutes. Give each one its own cell, with the resulting `Dataset` as the cell's last expression.

Never put `client.offline_query(...)` inside a `for` loop. A loop serializes jobs that each cost a revision, turns one slow cell into an unbounded one, makes the cell's rendered output meaningless (only the last iteration survives as the return value), and gives you nothing to click through to in the dashboard per-iteration. It also tempts you into suppressing the progress panels each iteration prints - which throws away the tracebacks you need when one of them fails.

```python
# CORRECT - one query, one cell, dataset returned bare
ds = client.offline_query(
    input={User.id: user_ids},
    output=FEATURES,
    dataset_name="fraud_investigation",
    recompute_features=True,
)
ds

# WRONG - N revisions, serialized, output clobbered each pass
for feats in feature_groups:
    d = client.offline_query(input={User.id: user_ids}, output=feats, recompute_features=True)
    d.revisions[-1].wait_for_completion()
```

If you're trying to work out which of several features breaks a query, add one cell per candidate rather than looping - each cell then has its own readable output, its own dashboard link, and can be re-run on its own. Better still, query the whole feature set once and read the failure message: it names the resolver and primary key that failed.

Relatedly, don't wrap Chalk calls in `contextlib.redirect_stdout(...)` to quiet their progress panels. It suppresses the error text at exactly the moment you need it. If a cell's output is too long for your terminal, raise `--max-output-bytes` or page through it with `chalk notebook output rows <notebook-id> <output-id> --offset N --limit M`.

## Pipe a SQL cell into a Python cell with `--result-variable`

A SQL cell can bind its result to a Python variable that later Python cells reference directly as a chalkdf-style dataframe (`.to_pandas()`, `.with_columns({...})`, filtering with `_`, etc.) without re-running the query. Set it from the CLI with `--result-variable`, and pair it with `--datasource` to hit a named source instead of Chalk SQL:

```bash
chalk notebook cell add <notebook-id> --kind sql --datasource postgres \
  --result-variable user_rows --no-cache \
  --source 'select id, is_fraud from users_demo order by id' --run
```

```python
# a later Python cell - user_rows is already bound, no re-query needed
user_ids = user_rows.to_pandas()["id"].tolist()
```

This is the cleanest way to ground a query in real primary keys: pull the ids in SQL, then feed them to `client.query`/`client.offline_query` in Python. Inventing ids with `range()` silently produces keys that join to no row, which surfaces as a misleading "required argument ... could not be computed" error naming an unrelated downstream resolver.

Use `--no-cache` (or `--max-staleness=0`) when the underlying table may have changed and you need fresh rows.

## Charts: always use Altair

Altair is the only charting library installed by default in the notebook kernel - matplotlib and plotly are not installed. Use it, and return the chart as the cell's last expression so it renders inline:

```python
import altair as alt

alt.Chart(df).mark_bar().encode(
    x="user.fraud_score",
    y="count()",
)
```

If you need a chart type or transform that isn't in the notebook's default environment, install the specific package with `!uv pip install <package>` (see below) rather than switching charting libraries.

## Installing packages: use `!uv pip install`, not `!pip install`

The kernel has both `uv` and `pip` available, but prefer `uv`: it resolves and installs significantly faster, which matters since you're often waiting on it interactively.

```python
!uv pip install some-package
```

Avoid `!pip install some-package` - it works, but is noticeably slower for no benefit in this environment.

## Further reading

- https://docs.chalk.ai/llms.txt - core concepts, optimized for AI assistants
- https://docs.chalk.ai/llms-full.txt - comprehensive docs reference
