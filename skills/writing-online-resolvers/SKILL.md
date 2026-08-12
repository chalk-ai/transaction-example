---
name: writing-online-resolvers
description: Use when writing, refactoring, or debugging Chalk `@online` Python resolvers — picking the right input/output signature (scalar, `Features[...]`, `DataFrame`, has-many), using `Now`, navigating relationships via dotted paths, choosing between Python resolvers and feature expressions (`F.http_post`, `_` expressions), handling nulls, tagging/environment/resource hints, and writing unit + integration tests for them.
---

# Writing Online Resolvers

## When to Use

- Writing a new `@online` Python resolver
- Choosing between a Python resolver and a feature expression (`F.http_post`, `_.foo`, SQL file)
- Picking the right input/output shape: scalar, `Features[...]`, `DataFrame[...]`, has-many
- Pulling fields from related namespaces via dotted feature paths
- Adding `Now` for "as-of" / point-in-time arithmetic (optional; mostly for time-dependent features like age calculations)
- Parsing protobuf / JSON / vendor payloads into many features at once
- Aggregating a has-many `DataFrame` down to a scalar (risk score, totals, ratios)
- Tagging resolvers, scoping to an environment, or routing to a specific machine type
- Writing unit + integration tests for resolvers

## Minimal Example

```python
from datetime import timezone
from chalk import online, Now
from src.models import Account

@online
def calculate_account_age(
    signed_up_at: Account.signup.signed_up_at,   # joins Account → Signup automatically
    now: Now,                                    # query "as-of" time; defaults to wall clock
) -> Account.account_age_days:
    if signed_up_at is None:
        return None
    if signed_up_at.tzinfo is None:
        signed_up_at = signed_up_at.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (now - signed_up_at).days
```

This single resolver supports both real-time and historical queries — pass `now=...` to `ChalkClient.query` and Chalk replays it for that point in time.

## When NOT to Use

- **The value already lives in a system you can call declaratively** — use a feature expression instead:
  - HTTP call → `F.http_post(...)` on the feature (see [Feature Expressions vs Python Resolvers](#feature-expressions-vs-python-resolvers))
  - SQL row → drop a `.chalk.sql` file in `src/resolvers/sql/`
  - Simple arithmetic across columns → `_.a + _.b` on the feature
- **Pure DataFrame transformation at `chalk apply` time** — use `@online(static=True)` with chalkdf instead. See `writing-static-chalkdf` skill.
- **Streaming source** (Kafka/Kinesis/PubSub) — use `make_stream_resolver`. See `chalk-streaming` skill.
- **Batch backfills from a warehouse** — use `@offline` resolvers or `chalk upload`.

---

## Resolver Signature Shapes

The `@online` decorator works on any function whose parameters and return are typed with Chalk feature references. The four shapes you'll actually use:

### 1. Scalar in → scalar out

```python
@online
def normalize_score(raw: User.raw_score) -> User.normalized_score:
    return min(max(raw, 0.0), 1.0)
```

Each input is a `Class.field`. Chalk resolves the value for that entity and passes it as the typed argument.

### 1b. No-argument resolver (constants / env lookups)

```python
@online
def get_platform_version() -> Config.platform_version:
    return os.getenv("PLATFORM_VERSION", "2.1.0")
```

Use when the value is a constant, comes from environment, or is derived from process-local state. Called once per query — not cached across requests; if you want caching, set `max_staleness` on the feature.

### 2. Cross-namespace input via dotted path

```python
@online
def derive_thing(
    signed_up_at: Account.signup.signed_up_at,        # Account → Signup join
    bi_score: Account.business_intelligence.score,    # Account → BI join
) -> Account.thing: ...
```

Chalk traverses the relationship at query time. Don't manually fetch the parent — declare the dotted path and Chalk resolves the join. Works for `has_one` and (when you select a *single* sub-feature) for any relation.

### 3. `Features[...]` — many features out in one shot

```python
from chalk import online, Features

@online
def parse_signup_response(
    body: Signup.signup_response_body,
) -> Features[
    Signup.signed_up_at,
    Signup.first_name,
    Signup.last_name,
    Signup.company_name,
    # ... 20 more
]:
    response = signup_pb2.GetSignupResponse()
    response.ParseFromString(body)
    info = response.information
    return Signup(
        signed_up_at=response.signed_up_at.ToDatetime() if response.HasField("signed_up_at") else None,
        first_name=info.first_name if info else None,
        last_name=info.last_name if info else None,
        company_name=info.company_name if info else None,
        # ...
    )
```

Use this for proto/JSON unpacking, vendor-payload parsing, or anywhere one input produces a fan-out of scalar features on the same entity. Return the feature class constructed with kwargs — Chalk extracts the fields listed in `Features[...]`.

### 4. `DataFrame[...]` in → scalar out (has-many aggregation)

```python
from chalk.features import DataFrame
from datetime import datetime

def _most_recent_score(profiles: DataFrame) -> int | None:
    try:
        rows = list(profiles)
        if not rows:
            return None
        most_recent = max(rows, key=lambda r: r.inserted_at or datetime.min)
        return most_recent.primary_score
    except Exception:
        return None

@online
def calculate_risk_score(
    credit_profiles_df: Account.credit_profiles,        # has-many DataFrame
    total_recent_txns_30d: Account.total_recent_txns_30d,
    age_days: Account.account_age_days,
) -> Account.risk_score:
    score = _most_recent_score(credit_profiles_df)
    # ... combine with other factors, return float
```

When you reference a has-many relation (`Account.credit_profiles`) as a parameter, Chalk passes you the related rows as a `DataFrame`. Iterate with `list(df)` and access fields by name (`row.inserted_at`), or use chalkdf methods. **Don't** call `.run()`, `.to_pandas()`, or anything that tries to materialize — at query time you're already holding real data; at plan time (static resolvers) you'd crash.

### 5. Has-many out — construct a `DataFrame[Child]`

```python
@online
def parse_account_individuals(
    body: Account.account_response_body,
    account_id: Account.customer_account_id,
) -> Account.individuals[                    # selects which Individual fields you produce
    Individual.email,
    Individual.full_name,
    Individual.date_of_birth,
]:
    response = kyc_pb2.GetAccountResponse()
    response.ParseFromString(body)
    return Account(
        individuals=DataFrame([
            Individual(
                email=ind.email_address,
                customer_account_id=account_id,
                full_name=ind.full_name,
                date_of_birth=datetime(
                    year=ind.date_of_birth.year,
                    month=ind.date_of_birth.month,
                    day=ind.date_of_birth.day,
                ),
            )
            for ind in response.individuals
        ])
    )
```

The return annotation `Account.individuals[Individual.field1, ...]` declares which child fields this resolver produces. The body returns the parent class with `individuals=DataFrame([Child(...), ...])`.

---

## The `Now` Magic Input

```python
from chalk import Now

@online
def f(ts: Account.signed_up_at, now: Now) -> Account.age_days: ...
```

- For real-time queries: `now` = current wall-clock time
- For point-in-time queries (`client.query(now=datetime(2024,1,1))`): `now` = the requested instant
- For offline / backtest queries: `now` = the row's timestamp

You write the resolver once and get correct behavior in all three. Always make timestamp inputs timezone-aware before subtracting (`tz=timezone.utc`) — wall-clock times often arrive naive.

---

## Feature Expressions vs Python Resolvers

Many resolvers customers write as Python should actually live on the feature class as an expression. Rule of thumb: **if it's declarative (call an HTTP endpoint, pluck a JSON path, do arithmetic on two columns), use an expression. If it's procedural (branching, iteration, library calls, parsing), use `@online`.**

### Pattern: HTTP body as a feature, Python resolver to parse it

```python
# models.py — declarative: Chalk makes the call, retries, caches
@features
class Signup:
    customer_account_id: Primary[str]
    signup_request: bytes
    signup_response_raw: HttpResponse[bytes] = F.http_post(
        get_url("/server.v1.SignupService/GetSignup"),
        headers=get_headers(),
        body=_.signup_request,
        timeout=timedelta(seconds=5),
    )
    signup_response_body: bytes = _.signup_response_raw.body
    # ... scalar fields populated by the parser below ...

# resolvers/proto_parsers.py — procedural: parse bytes → many features
@online
def parse_signup_response(body: Signup.signup_response_body) -> Features[...]:
    response = signup_pb2.GetSignupResponse()
    response.ParseFromString(body)
    return Signup(...)
```

Why split it: the expression form gets you connection pooling, retries, observability, and timeouts for free. The Python resolver only handles what genuinely needs Python (proto deserialization).

### Useful `F.` functions worth knowing before reaching for Python

| Need | Use |
|---|---|
| HTTP call | `F.http_post(url, headers=..., body=..., timeout=...)`, `F.http_get(...)` |
| JSON path extraction | `F.json_value(_.payload, "$.user.email")` |
| Unix timestamp → datetime | `F.from_unix_seconds(_.ts)`, `F.from_unix_milliseconds(_.ts_ms)` |
| Proto deserialize | `F.proto_deserialize(_.bytes, MyProto)` (use `F.recover` to swallow bad bytes) |
| Conditional | `F.if_then_else(cond, if_true, if_false)` |
| Coalesce | `F.coalesce(_.a, _.b, default)` |

Reach for a Python `@online` when none of these compose to what you need.

---

## Decorator Options

```python
@online                                       # bare — runs in default env, no tags, no machine hint
@online(tags=["pii"])                         # gates by tag at query time
@online(environment="production")             # only runs in this env
@online(machine_type="cpu-large")             # route to a specific machine class
@online(resource_hint="cpu")                  # scheduling hint: "cpu", "io", or "gpu"
@online(resource_group="streaming")           # pin to a named worker resource group
@online(static=True)                          # runs once at chalk apply, builds a chalkdf plan
@online(owner="team-fraud")                   # surfaces in dashboards, lineage, and alerts
@online(timeout="5s")                         # per-call timeout; resolver fails if exceeded
```

- **Default to bare `@online`.** Add options only when there's a concrete reason.
- `resource_hint`: `"gpu"` for ML inference; `"cpu"` is common for compute-heavy aggregations on has-many DataFrames; `"io"` for I/O-bound work. The hint is **advisory** — actual routing happens in the worker dispatcher, not at plan time.
- `static=True` is a different beast — see the `writing-static-chalkdf` skill.

---

## Null Handling

Chalk represents missing data as `None`. Adopt these conventions and most footguns disappear:

1. **Type all nullable inputs/outputs `T | None`.** If a resolver can fail to produce a value, return `None` instead of raising.
2. **Guard before arithmetic.** `if signed_up_at is None: return None` then continue.
3. **Iterate has-many DataFrames defensively:**
   ```python
   try:
       rows = list(df)
       if not rows:
           return None
       ...
   except Exception:
       return None
   ```
4. **Don't swallow errors silently in production-critical resolvers.** Either return `None` *and* log, or let it raise. A bare `except: pass` hides real bugs.
5. **Coalesce at the call site, not inside the resolver,** when the caller has better context for the default:
   ```python
   txn_factor = _normalize_txn_factor(total_recent_txns_30d or 0)
   ```

---

## Organizing Resolvers

Conventions that scale well in real codebases:

- **Small projects (<50 resolvers): a single `resolvers.py` is fine.** Larger codebases benefit from splitting by domain: `account_resolvers.py`, `risk_resolvers.py`, `proto_parsers.py`. Pick the cut that matches your team's mental model.
- **Helpers prefixed with `_`** and kept in the same file as their caller (or in a `helpers/` subdir if shared across domains).
- **Module-level singletons for DB clients, HTTP sessions, and auth helpers** — instantiated once at import time, reused across resolvers:
  ```python
  # src/datasources.py — DB clients
  pg = PostgreSQLSource(name="financials_db")
  snowflake = SnowflakeSource(name="warehouse")

  # src/utils/grpc_requests.py — HTTP helpers used inside F.http_post(...)
  def get_url(path: str) -> str: ...
  def get_headers() -> dict[str, str]: ...
  ```
  Don't construct clients inside the resolver body — you'll exhaust connection pools and lose retry/instrumentation benefits.
- **SQL resolvers** live as `.chalk.sql` files in `src/resolvers/sql/` and don't need a Python wrapper. Chalk discovers them automatically.
- **Imports at the top of every resolver file:**
  ```python
  from chalk import online, Now, Features
  from chalk.features import DataFrame, _
  from datetime import datetime, timedelta
  from src.models import ...
  ```

### Logging

Import `chalk_logger` for resolver-side logs that flow into Chalk's observability:

```python
from chalk.clogging import chalk_logger

@online
def parse_vendor_response(body: Vendor.response_body) -> Vendor.parsed_id:
    try:
        payload = vendor_pb2.Response.FromString(body)
        return payload.id
    except Exception as e:
        chalk_logger.info(f"vendor parse failed: {e}")
        return None
```

- Prefer `return None` + log over `raise` for known-failure cases — keeps the rest of the feature graph computable.
- Don't log raw PII or vendor payloads — log identifiers and error classes.

### Request-scoped metadata via `ChalkContext`

Use `ChalkContext.get(key)` to read metadata the caller attached at query time via `ChalkClient.query(context={...})`. Useful for correlation IDs, tenant flags, A/B variants — anything that varies per request without changing the resolver signature:

```python
from chalk import ChalkContext

@online
def get_pricing_tier(base: Account.base_tier) -> Account.pricing_tier:
    if ChalkContext.get("experiment") == "premium-rollout":
        return "premium"
    return base
```

---

## Testing Resolvers

### Unit test — call the function directly

A Python resolver is a regular Python function. Test the logic without spinning up Chalk:

```python
# tests/unit_test.py
from src.resolvers.risk_resolvers import derive_composite_score

def test_composite_score_balances_internal_and_external():
    score = derive_composite_score(
        owners_leadership_score=80,
        operational_maturity_score=70,
        customer_base_score=90,
        business_model_score=60,
        industry_macro_score=75,
    )
    assert score == 78.5
```

Fast feedback, no network, runs in a normal pytest loop. Cover edge cases (None inputs, empty DataFrames, boundary values).

### Integration test — `ChalkClient.check` against a branch

For anything that depends on relationships, expressions, or aggregations, exercise the full feature graph against a deployed branch:

```python
# tests/conftest.py
import os, pytest
from chalk.client import ChalkClient

@pytest.fixture(scope="session")
def chalk_client():
    branch = os.environ.get("GITHUB_REF_NAME") or True   # True → auto-branch from local commit
    yield ChalkClient(branch=branch)

# tests/integration_test.py
from datetime import datetime, timezone
from src.models import Account

def test_risk_score(chalk_client):
    chalk_client.check(
        input={Account.customer_account_id: "acct_123"},
        assertions={Account.risk_score: 42.0},
        now=datetime(2024, 6, 1, tzinfo=timezone.utc),
        show_table=True,
        tags=["integration_testing"],
    )
```

- `branch=True` auto-creates a branch from your local commit so PR runs don't collide.
- `now=...` exercises the same code path that backtesting uses — a good way to catch tz-naive bugs.
- `assertions={...}` does an exact match; for floats, prefer `pytest.approx`.

### Wiring in CI

Run unit tests on every PR (no Chalk credentials needed). Run integration tests against a branch deployment with the `integration_testing` tag so production resolvers aren't accidentally invoked.

---

## Common Mistakes

| Mistake | Fix |
|---|---|
| Calling `df.run()` / `df.to_pandas()` inside a static resolver | At plan time you're operating on a placeholder; use chalkdf operations and return a lazy frame |
| Subtracting a naive `datetime` from `Now` | Coerce `tzinfo=timezone.utc` on both sides before arithmetic |
| Returning a dict / tuple from a `Features[...]` resolver | Return the feature class constructed with kwargs: `return Account(field=..., field=...)` |
| Manually joining parent → child by querying inside the resolver | Use a dotted path input (`Account.signup.signed_up_at`) and let Chalk resolve the join |
| Wrapping every `F.http_post` in a Python resolver | Keep the HTTP call as an expression on the feature; only write Python to parse the response body |
| `@online` decorator missing on the function | Bare `def f(...)` is a helper, not a resolver — Chalk won't register it |
| Catching `Exception` and returning a default silently | Either return `None` *and* log, or let it raise; silent defaults hide schema drift |
| One giant `resolvers.py` with 80 functions | Split by domain (`account_resolvers.py`, `risk_resolvers.py`, `proto_parsers.py`) |
| Per-call gRPC client construction | Use module-level singletons or fold the call into an `F.http_post` expression |

---

## Common Errors

| Error | Cause | Fix |
|---|---|---|
| `Resolver returned None but field is non-nullable` | Output type is `int` but resolver returns `None` on some path | Make the output `int \| None`, or branch so all paths return an int |
| `unable to deserialize Chalk DataFrame` at `chalk apply` | Static resolver returned an eagerly-materialized frame | Don't call `.run()`/`.to_pandas()`; return a lazy chalkdf — see `writing-static-chalkdf` skill |
| `LazyFramePlaceholder: unsupported type` | A static resolver used an op chalkdf can't serialize | Switch to a supported chalkdf op or move logic to a non-static `@online` |
| `Features[X, Y] resolver returned object missing attribute X` | Constructor missing a kwarg listed in `Features[...]` | Pass every listed feature as a kwarg, even if it's `None` |
| Resolver runs in dev but not in prod | `@online(environment="staging")` or tag gate | Check the decorator; remove the gate or add prod to the allow-list |
| `tzinfo` comparison TypeError | Naive vs aware datetime arithmetic | Coerce both sides to `tzinfo=timezone.utc` before subtracting |
| Has-many `DataFrame` arg is `None` instead of empty | Caller didn't request the relation; resolver wasn't given the data | Check the query's output set; ensure the relation field is requested or has a default |
| `ChalkClient.check` assertion fails by tiny epsilon | Exact float comparison | Use `pytest.approx(expected, rel=1e-6)` or assert within a tolerance |

---

## Further reading

- `BASE_CHALK_PROMPT.md` — core Chalk concepts (features, relationships, queries)
- `writing-static-chalkdf` skill — for `@online(static=True)` chalkdf resolvers
- `chalk-streaming` skill — for `make_stream_resolver` (Kafka/Kinesis/PubSub)
- https://docs.chalk.ai/docs/python-resolvers — canonical reference
- https://docs.chalk.ai/llms.txt — full docs optimized for AI assistants
