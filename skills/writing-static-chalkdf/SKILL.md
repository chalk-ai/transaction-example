---
name: writing-static-chalkdf
description: Use when writing Chalk @online(static=True) resolvers with chalkdf — porting pandas/numpy resolver logic to static acceleration, exploding has-many relations, window functions (cum_sum/shift/row_number/.over), rolling & exact-quantile reformulations, join_asof, emitting has-many or has_one outputs, sharding at scale (use_metaplanner/num_shards), accessing Now, or debugging chalk apply serialization errors like "unable to deserialize Chalk DataFrame" or "LazyFramePlaceholder: unsupported type".
---

# Writing Static ChalkDF Resolvers

## Overview

`@online(static=True)` resolvers **run once at plan time** (`chalk apply`), not at query time. The function executes with a real `DataFrame` backed by a `LazyFramePlaceholder`, building up a lazy DAG. That DAG is then serialized to proto and stored — at query time Chalk executes the pre-built plan against real data.

**What this means:**
- **Arbitrary Python is allowed** in the function body — loops, helper functions, constants, conditionals based on Python values all work. They run at plan time and just build up more plan nodes.
- **The return value must be a lazy `DataFrame`** (an unexecuted plan). Never call `.run()`, `.to_pandas()`, or anything that materializes data mid-chain — these try to execute against placeholder data at plan time and fail.
- **Only serializable chalkdf nodes can be in the plan.** Some operations produce proto nodes the serializer doesn't support, causing a cryptic error at `chalk apply` time (not at runtime).

This is why Python loops like `for _ in range(5): groups = pointer_jump(groups)` work fine — they run at plan time and produce a larger (but valid) plan graph.

> **chalkpy version matters — this guide targets recent chalkpy (from `development/chalk/chalk` main).** The serializable surface changed over time, so treat any "safe/unsafe" list as version-dependent. On recent versions:
> - **Window functions (`.over()` / `cum_sum` / `row_number` / `.shift()`) serialize** and are the preferred way to do per-group / running / rolling / LAG computations — see **Window Functions** below.
> - **Self-referential self-joins may *not* serialize** ("unsupported operand DataFrame"), so the self-join patterns further down (row-numbering, pointer-jumping) are an **older-chalkpy fallback**, kept for reference. On chalkpy ≤ 2.132.x it's the reverse (windows don't serialize, self-joins do).
>
> There is **no static list of what serializes** — the ground truth is empirical (`to_proto` + `chalk apply`); see **Verifying Serializability** below. When in doubt, test with *your deployed chalkpy*.

> **Is a full chalkdf rewrite even the right lever?** Reach for it when **data volume** — large has-many / multi-join / windowed inputs that OOM or run slow in the Python worker — is what hurts, because a static chalkdf plan is the only path that **spills to disk and shards**. If the cost is instead per-row Python *logic*, a plain inline `F.*` / `_.` expression or the symbolic-Python accelerator is simpler and enough. The **`chalk-resolver-acceleration`** skill covers that decision (accelerate / express / rewrite / leave) and what won't symbolically translate.

## Resolver Skeleton

```python
from chalk import online
from chalk.features import DataFrame, Now, _
import chalk.functions as F
from datetime import datetime
from src.models import Parent, Child

@online(static=True)
def my_resolver(
    df: DataFrame[
        Parent.id,
        Parent.some_field,
        Now,                          # include if you need current time
        Parent.children[              # has-many: declare subset of fields
            Child.id,
            Child.created_at,
        ],
    ],
) -> DataFrame[Parent.id, Parent.output_field]:
    return _impl(df)   # helper delegation is allowed
```

## Column Naming

### Feature class → column name conversion

Chalk converts feature class names to `snake_case` dotted paths:

```
ClassName.field_name  →  "class_name.field_name"
```

Examples:
```python
str(Parent.id)          # → "parent.id"
str(Child.created_at)   # → "child.created_at"
str(MyThing.amount)     # → "my_thing.amount"
str(Parent.children)    # → "parent.children"   (the list column itself)
```

Rule: **CamelCase class name → snake_case prefix**, then `.field_name`.

### Has-many struct field names

After `explode()`, the list column becomes a struct. Fields inside the struct use the **full dotted feature name** — you access them via `getattr` chains (see below). When building test data, struct field keys are bare names (no prefix):

```python
pa.struct([("id", pa.string()), ("created_at", pa.timestamp("us")), ...])
```

### In resolver code

```python
# Temp/intermediate columns: plain strings
.with_columns({"slot": ..., "rn": ...})

# Output features: alias to full feature string
.select(_.computed.alias("parent.output_field"))
# Or: .select(_.computed.alias(str(Parent.output_field)))
```

### `Now` column name

`Now` in `DataFrame[..., Now]` injects column `"__chalk__.now"`. Access via `df.col()`, not `_`:

```python
df.col("__chalk__.now")    # correct
# _.chalk_now              # WRONG
```

## Accessing Has-Many Relations

**Pattern:** save a skeleton before exploding, explode, work on flat df, join skeleton back.

```python
# 1. Save skeleton (key cols + now, before exploding changes the row cardinality)
df_skeleton = df.with_columns({
    "parent.id": _.parent.id,
    "__chalk__.now": df.col("__chalk__.now"),
})

# 2. Explode the has-many column
df_exploded = df.explode(str(Parent.children))

# 3. Extract nested struct fields via getattr chains
df_flat = df_exploded.with_columns({
    "child.created_at": getattr(getattr(_, str(Parent.children)), str(Child.created_at)),
    "child.created_date": getattr(getattr(_, str(Parent.children)), str(Child.created_date)),
})

# 4. Compute on df_flat ...

# 5. Join results back to skeleton (one row per parent)
final = df_skeleton.join(result, on=["parent.id", "__chalk__.now"], how="left")
```

## Safe vs Unsafe Operations

### Safe
- `filter()`, `join()`, `with_columns()`, `select()`, `drop()`, `rename()`, `fill_null()`, `explode()`
- `order_by()`, `with_unique_id()`, `distinct_on()`, `union_all()`
- `group_by().agg()` / `.agg([keys], expr)` with: `count()`, `max()`, `min()`, `sum()`, `mean()`, `_.col.approx_percentile(q)`
- **Window functions (recent chalkpy):** `F.cum_sum(col).over(partition_by=[…], order_by="k")`, `F.row_number().over(…)`, `_.col.shift(N).over(…)`, `F.over(_.col.max()/min(), partition_by=[…])` — the preferred replacement for the self-join patterns; see **Window Functions** below
- `F.unix_seconds()`, `F.from_unix_seconds()`, `F.cast()`, `F.date_trunc(col, unit)`, `F.floor()`, `F.ceil()`, `F.bankers_round()`
- `F.coalesce()`, `F.if_then_else()`, `F.is_not_null()`, `F.is_null()`
- Arithmetic `+`, `-`, `*`, `/`; Boolean `&`, `|`, `~`; Comparisons `==`, `!=`, `>`, `<`, `>=`, `<=`
- `_.col.regexp_like(pattern)`, `_.col.lower()`, `_.list_col.contains(value)`
- Helper functions: `return _impl(df)` delegation is fine

### Unsafe → Alternatives

| Broken | Use instead |
|--------|-------------|
| `_.col.is_in([list])` | `(_.col == "a") \| (_.col == "b") \| ...` |
| `F.total_seconds(timedelta)` | `F.unix_seconds(t1) - F.unix_seconds(t2)` |
| `F.year(col)`, `F.month(col)` | Integer slot: `F.unix_seconds(F.cast(col, datetime)) / MONTH_SECS` |
| `_.col.stddev()` | Not serializable — remove or approximate |
| **window `WindowExpr` frames** (`F.trailing(n)`, `F.rows_between`, `F.cumulative()` literal) | frames don't serialize — but plain `.over()` window fns *do* (recent chalkpy); use `cum_sum` + `shift` for rolling (see Window Functions) |
| **self-referential self-join** (`df.join(df, …)`) *(recent chalkpy)* | window functions `.over()` — self-joins execute but fail to serialize ("unsupported operand DataFrame"); the self-join patterns below are an older-version fallback |
| `.cast(<python primitive>)` e.g. `.cast(float)` | "Could not infer literal type" — floatify via `col * 1.0`, keep int denominators; `F.cast(col, datetime)` (arrow type) is fine |
| `order_by(("col", "desc"))` direction tuple | negate the key (`neg = col * -1`) + ascending; direction tuples deserialize as a phantom `desc` column |
| exact `quantile()` as an agg | `_.col.approx_percentile(q)`, or exact rank-based order statistics (see Reformulation Patterns) |
| `from_pandas()` / `MaterializedDataFrame` | Pure `_` DSL only |
| `lit("str", pa.large_utf8())` | Use separate `@online` resolver for string constants |
| F-function wrapping `agg` expression | Aggregate first (plain `.max()`), then `with_columns(F.unix_seconds(...))` |

## Common Patterns

### Windowed filter using `Now`

```python
THIRTY_DAYS_IN_SECONDS = 30 * 24 * 3600

df_30d = df_flat.filter(
    (df_flat.col("child.created_at") >= F.from_unix_seconds(
        F.unix_seconds(F.date_trunc(df_flat.col("__chalk__.now"), "day"))
        - THIRTY_DAYS_IN_SECONDS
    ))
    & (df_flat.col("child.created_at") < df_flat.col("__chalk__.now"))
)
```

### Double aggregation (e.g. max daily count)

```python
result = (
    df_30d
    .agg(                                      # first: count per day per parent
        ["child.created_date", "parent.id", "__chalk__.now"],
        _.count().alias("daily_count"),
    )
    .agg(                                      # then: max across days
        ["parent.id", "__chalk__.now"],
        _.daily_count.max().alias("parent.max_daily_count"),
    )
)
```

### Deduplication (keep earliest-created, smallest-id)

```python
min_created = items.agg(
    ["parent.id", "start", "end"],
    _.created_at.min().alias("min_created_at"),
)
items = (items
    .join(min_created, on=["parent.id", "start", "end"], how="inner")
    .filter(_.created_at == _.min_created_at))

min_id = items.agg(
    ["parent.id", "start", "end", "created_at"],
    _.item_id.min().alias("min_item_id"),
)
deduped = (items
    .join(min_id, on=["parent.id", "start", "end", "created_at"], how="inner")
    .filter(_.item_id == _.min_item_id))
```

### Row numbering + self-join running aggregation

> ⚠️ **Older-chalkpy fallback.** On recent chalkpy this self-join fails to serialize ("unsupported operand DataFrame") — use `F.row_number()` + `F.cum_sum().over(…)` instead (see **Window Functions**). Kept here for chalkpy ≤ 2.132.x.

```python
ordered = deduped.order_by("start", "end").with_unique_id("rn")

prev_max = (
    ordered
    .join(ordered, on=["parent.id"], how="left", right_suffix="_prev")
    .filter(_.rn_prev < _.rn)
    .agg(["parent.id", "rn"], _.end_prev.max().alias("prev_max_end"))
)
with_max = ordered.join(prev_max, on=["parent.id", "rn"], how="left")
```

### Transitive closure via pointer jumping (O(log n) iterations)

> ⚠️ **Older-chalkpy fallback** (same self-join serialization caveat as above). No pure-window equivalent — if you need transitive closure on recent chalkpy, verify the self-join serializes on *your* deployed version first, or restructure the problem.

```python
def pointer_jump(groups):
    lookup = (groups
        .select("parent.id", "rn", "group_id")
        .rename({"rn": "group_id", "group_id": "parent_group_id"}))
    return (groups
        .join(lookup, on=["parent.id", "group_id"], how="left")
        .with_columns({
            "group_id": F.if_then_else(
                F.is_not_null(_.parent_group_id) & (_.parent_group_id < _.group_id),
                _.parent_group_id,
                _.group_id,
            )
        })
        .select("parent.id", "rn", "group_id", ...))

for _ in range(5):   # 5 iterations → handles chains up to 32 hops
    groups = pointer_jump(groups)
```

### Equality chains (replaces `is_in`)

```python
# Instead of: _.category.is_in(["a", "b", "c"])
is_match = (_.category == "a") | (_.category == "b") | (_.category == "c")
```

### F-functions after agg, not inside

```python
# WRONG — F inside agg fails serialization:
.agg(["parent.id"], F.unix_seconds(F.cast(_.date.max(), datetime)).alias("secs"))

# CORRECT — aggregate plain, then apply F:
.agg(["parent.id"], _.date.max().alias("max_date"))
.with_columns(F.unix_seconds(F.cast(_.max_date, datetime)).alias("secs"))
```

## Window Functions (preferred over self-joins, recent chalkpy)

On recent chalkpy, `.over(partition_by=[…], order_by="k")` window functions serialize — cleaner and cheaper than the self-join patterns above, and self-joins may not serialize at all. Reach for these first.

Two rules: use **non-leading-underscore** intermediate column names (`_.cs`, not `_._cs`), and a window agg's input must be a **plain column, not an inline expr** (materialize `if_then_else(...)` as a column first, then aggregate it).

```python
W = dict(partition_by=["parent.id"], order_by="t")

# running total + rank (replaces the row-numbering self-join)
d = d.with_columns({"running_total": F.cum_sum(d.col("x")).over(**W),
                    "rn":            F.row_number().over(**W)})

# LAG — value N rows back (shift serializes; only frames don't)
d = d.with_columns({"x_prev": _.x.shift(N).over(**W)})

# latest row per group (replaces max_by / a self-join)
d = d.with_columns({"max_t": F.over(_.t.max(), partition_by=["parent.id"])})
latest = d.filter(d.col("t") == d.col("max_t"))
```

## Reformulation Patterns

The pandas ops that look un-portable but aren't.

### Rolling window → cum_sum + shift (sliding window via prefix sums)
`rolling(N).mean()` needs a frame (unsupported). Trailing-N sum = running-total-now − running-total-N-rows-ago; mean = that ÷ running count:
```python
W = dict(partition_by=["g"], order_by="t")
d = d.with_columns({"cs": F.cum_sum(d.col("x")).over(**W),
                    "cc": F.cum_sum(F.if_then_else(F.is_not_null(d.col("x")), 1, 0)).over(**W)})  # materialize indicator first
d = d.with_columns({"cs_lag": _.cs.shift(N).over(**W), "cc_lag": _.cc.shift(N).over(**W)})
rsum = d.col("cs") - F.coalesce(d.col("cs_lag"), 0.0)   # coalesce → first N rows = cumulative (min_periods=1)
rcnt = d.col("cc") - F.coalesce(d.col("cc_lag"), 0)
d = d.with_columns({"roll_mean": F.if_then_else(rcnt > 0, rsum / rcnt, None)})   # null-aware
```

### Exact quantile → rank-based order statistics
Exact quantile is holistic (must sort the whole group) so it's not a mergeable agg — chalkdf offers `approx_percentile(q)` (t-digest; can be ~30% off on small, heavy-tailed windows). Rebuild it exactly *and* spillably via ranking (`pos = q*(n-1); p = (sorted[floor(pos)] + sorted[ceil(pos)])/2`):
```python
j   = j.with_columns({"rn": F.row_number().over(partition_by=["g"], order_by="amt")})
grp = j.agg(["g"], _.count().alias("n"))
jr  = j.join(grp, on=["g"]).with_columns({"lo": F.floor(q*(_.n-1))+1, "hi": F.ceil(q*(_.n-1))+1})
jr  = jr.with_columns({"c_lo": F.if_then_else(jr.col("rn")*1.0 == jr.col("lo"), jr.col("amt"), 0.0),  # rank as FLOAT
                       "c_hi": F.if_then_else(jr.col("rn")*1.0 == jr.col("hi"), jr.col("amt"), 0.0)})
os  = jr.agg(["g"], jr.col("c_lo").sum().alias("a_lo"), jr.col("c_hi").sum().alias("a_hi"))
os  = os.with_columns({"p": (os.col("a_lo") + os.col("a_hi")) / 2.0})   # exact midpoint quantile, byte-matches pandas
```

### As-of / forward-fill → join_asof
```python
# both sides MUST be partitioned by the by-cols: stamp with F.over(min) then order_by
left  = left.with_columns({"_pl": F.over(_.t.min(),  partition_by=["g"])}).order_by("g", "t")
right = right.with_columns({"_pr": F.over(_.rt.min(), partition_by=["g"])}).order_by("g", "rt")  # distinct stamp name per join_asof
out   = left.join_asof(right, left_on="t", right_on="rt", by=["g"], strategy="backward")
# note: join_asof DROPS the right on-key from its output — carry a copy column if you need it downstream
```

## Output Contracts

The Overview covers reading has-many *inputs*; here's writing *outputs*.

### has-many output (emit a has-many edge)
```python
) -> DataFrame[Parent.id, Parent.hm_edge]:   # BARE edge — no [...] subscript
    packed = out.with_columns({"_child": F.struct_pack({
        str(Child.id): ..., str(Child.field): ..., str(Child.report_date): ...,   # FeatureTime column LAST
    })})
    return packed.agg([str(Parent.id)], F.array_agg(packed.col("_child")).alias(str(Parent.hm_edge)))
```
Struct field order **must equal the child class definition order with the FeatureTime moved LAST** — names/types matching isn't enough; order is validated (else "output schema does not match declared schema"). Requires chalkpy with has-many-output support (chalk#3584 / chalk-private#38794).

### has_one output (emit a single child per parent)
Output columns named by the **projection path** `str(Parent.one_edge.field)`, NOT the child class `str(Child.field)`:
```python
) -> DataFrame[Parent.id, Parent.one_edge.field_a, Parent.one_edge.field_b]:
    return out.select(str(Parent.id), str(Parent.one_edge.field_a), str(Parent.one_edge.field_b))
```
`to_proto` serializes either naming; only the engine's output `select` enforces the projection-path form ("Column 'parent_one_edge.field' does not exist").

## Verifying Serializability (the empirical loop)
There's no static list of what serializes — test it:
1. **`plan.to_proto()`** locally (needs only `chalkpy` + `chalkdf`). Raises → won't serialize. ⚠️ **false-negatives exist** — some things pass locally but the branch's stricter deserializer rejects them (and rarely vice-versa); necessary, not sufficient.
2. **Isolate the suspect op** in a ~5-line synthetic `DataFrame.from_arrow(...)` and `to_proto`/apply *just that* — seconds, not minutes on the whole resolver.
3. **`chalk apply` + run a query** — ground truth; the error message names the culprit.

## Verifying Parity (non-negotiable)
A subtle port bug **silently changes the numbers**. Diff every output against the original pandas logic **on real entities**, not just synthetic — synthetic misses real-world category filters and edge cases (empty inputs, tiny/huge values, dormant entities). Aim for byte-exact. "Compiled and ran" is not the bar — especially for features feeding a model, where train/serve skew degrades predictions silently.

## Scaling to Large Populations
Static resolvers **spill automatically** (no flag). To run an offline/scheduled query over a large population without OOM:
- **`use_metaplanner=True` + `num_shards=N`** on `offline_query` (or set `num_shards` on the `ScheduledQuery`). `num_shards` alone does NOT shard — the query runs on one box (`num_computers=1`); the metaplanner is what fans the spine onto separate computers.
- Use **few fat shards** (~`population / 100k`), not many thin — pod spin-up/coordination overhead dominates once shards are small.
- **Bound heavy has-many inputs** with `after(days_ago=…)` in the input projection — only scan the window the output actually depends on. This is often the single biggest memory win.

## Local Testing

Static resolvers are fully testable locally — no Chalk server needed for logic tests. Only serialization requires `chalk lint`.

### Setup

```bash
uv pip install chalkdf
```

### Test logic by calling `_impl` directly (manual)

Extract resolver logic into a `_impl(df)` helper, build a synthetic `DataFrame`, call it, assert on the result:

```python
import pyarrow as pa
from chalkdf import DataFrame
from datetime import datetime, date

child_type = pa.struct([
    ("id", pa.string()),
    ("created_at", pa.timestamp("us")),
    ("created_date", pa.date32()),
])

table = pa.table({
    "parent.id": ["p1", "p2"],
    "__chalk__.now": pa.array(
        [datetime(2024, 2, 1), datetime(2024, 2, 1)],
        type=pa.timestamp("us"),
    ),
    "parent.children": pa.array(
        [
            [
                {"id": "c1", "created_at": datetime(2024, 1, 10), "created_date": date(2024, 1, 10)},
                {"id": "c2", "created_at": datetime(2024, 1, 10), "created_date": date(2024, 1, 10)},
            ],
            [
                {"id": "c3", "created_at": datetime(2024, 1, 20), "created_date": date(2024, 1, 20)},
            ],
        ],
        type=pa.list_(child_type),
    ),
})

df = DataFrame.from_arrow(table)
result = _impl(df).to_pandas()
assert result.loc[result["parent.id"] == "p1", "parent.max_daily_count"].iloc[0] == 2
print(result)
```

### Building has-many test data

Has-many columns are `pa.list_(pa.struct([...]))`. Struct field keys are **bare names** (no feature prefix):

```python
item_type = pa.struct([
    ("id", pa.string()),
    ("start", pa.date32()),
    ("end", pa.date32()),
    ("created_at", pa.timestamp("us")),
])

table = pa.table({
    "parent.id": ["p1"],
    "parent.items": pa.array(
        [[
            {"id": "i1", "start": date(2024, 1, 1), "end": date(2024, 1, 3), "created_at": datetime(2024, 1, 1)},
            {"id": "i2", "start": date(2024, 1, 3), "end": date(2024, 1, 5), "created_at": datetime(2024, 1, 2)},
        ]],
        type=pa.list_(item_type),
    ),
})
```

### Test serialization

```bash
chalk lint        # traces all resolvers and checks proto serialization
```

Logic errors surface in `.to_pandas()`. Serialization errors only appear at `chalk lint` / `chalk apply` time.

## Common Error Messages

| Error | Cause | Fix |
|-------|-------|-----|
| `unable to deserialize Chalk DataFrame from encoded plan` | F-function inside `agg()` | Move F-calls to `with_columns()` after aggregation |
| `LazyFramePlaceholder: unsupported type <X>` | Unsupported node (stddev, WindowExpr, etc.) | See Unsafe table |
| `AttributeError: 'MaterializedDataFrame'` | `is_in([list])` or `.run()` mid-chain | Use equality chains; never materialize |
| `ValueError: The parent for cast should be an underscore` | `lit("str", pa.large_utf8())` | Use separate `@online` resolver for string mapping |
| `Expression must be field access or constant` | window agg over an inline expr (`cum_sum(if_then_else(...)).over(...)`) | materialize the expr as a column first, then aggregate the column |
| output schema / `does not match declared schema` (has-many out) | struct field order ≠ child class definition order (FeatureTime not last) | reorder `struct_pack` fields to child def order, FeatureTime LAST |
| `Column 'parent_edge.field' does not exist` (has_one/has-many out) | output columns named by child class instead of projection path | name outputs `str(Parent.edge.field)`, not `str(Child.field)` |
| `Schema is not partitioned by the by columns` (join_asof) | join_asof inputs not partition-stamped | stamp both sides `F.over(_.k.min(), partition_by=[…])` + `.order_by(…)`; distinct stamp name per join_asof |
| self-join runs locally but `unsupported operand DataFrame` on apply | self-referential self-join on recent chalkpy | rewrite with `.over()` window functions (see Window Functions) |
