---
name: chalk-resolver-acceleration
description: Use when optimizing Chalk feature-pipeline latency — deciding whether to accelerate a Python resolver, migrate it to inline `F.*` / underscore (`_.`) expressions, or leave it as-is; recognizing code that fundamentally won't statically accelerate (module globals, third-party/C libraries, dynamic dicts, reflection, I/O) so you restructure instead of fighting it; and preserving output parity when moving logic from Python into the engine.
---

# Accelerating Chalk Resolvers & Migrating to Inline Expressions

A conceptual playbook for making feature pipelines fast: the *why*, the *strategies*, and — most
importantly — the *limits*, so you can tell when a piece of logic will never accelerate and
restructure (or leave) it instead of burning time.

## When to Use

- Optimizing the latency of an online (or offline) feature query
- Reviewing a new or existing `@online` / `@offline` Python resolver for performance
- Deciding between a Python resolver and an inline feature expression
- Triaging which un-accelerated resolvers are worth fixing
- Recognizing code that won't statically accelerate, and choosing how to restructure around it
- Moving blocking network I/O off the Python worker path
- Preserving output parity when changing how a model-feeding feature is computed

---

## 1. The core idea: where your code runs

Every feature value is produced one of a few ways, and the difference is almost entirely about **where
the computation physically runs**:

| Mechanism | Where it runs | Cost |
|---|---|---|
| **Inline expression** (`F.*` functions + `_.` references) | In the engine, compiled to native/vectorized code | Lowest — no Python, no thread hop, vectorizes over rows |
| **Statically-accelerated Python resolver** | In the engine — the resolver's Python is symbolically translated to engine ops | Low — same in-engine execution, written as Python |
| **Static chalkdf resolver** (`@online(static=True)`, body written in chalkdf `DataFrame` ops) | In the engine — an explicit lazy plan, **spills to disk + shards** | Low — handles has-many/join volumes too big for a Python worker's RAM |
| **Non-accelerated Python resolver** | On a Python worker thread, per row | Highest — GIL-bound, thread/serialization overhead, no vectorization |

The optimization goal is simple: **move work out of the Python worker path and into the engine.**
You have three levers:

1. **Convert** an existing Python resolver so the static accelerator can translate it,
2. **Express** the logic directly as an `F.*` / `_.` expression (expressions are *always* in-engine —
   there's no "will it convert?" question), or
3. **Rewrite** the resolver body into chalkdf `DataFrame` expressions under `@online(static=True)` —
   an explicit lazy plan the engine runs natively, and the only lever that **spills to disk** and
   **shards**. Reach for it when the logic is too heavy for a single inline expression *and* the
   Python version OOMs or is too slow on large has-many / multi-join / windowed inputs. See the
   **`writing-static-chalkdf`** skill for the mechanics (explode, join_asof, window functions,
   struct-pack outputs, serialization gotchas, sharding).

Expressions are the stronger of the first two levers. A Python resolver that accelerates is great; an
expression *can't* fail to accelerate. When designing new features, reach for expressions first — and
for the chalkdf rewrite (lever 3) when the data volume, not the per-row logic, is what hurts.

---

## 2. The mental model: what the engine can translate

The accelerator works by **symbolically executing your Python** and lowering it to a graph of engine
operations. It understands a *subset* of Python — roughly: **pure value transformations over typed
scalars, typed structs, and typed collections.** The further your code drifts from that, the less
likely it converts.

Internalize this one sentence and most decisions become obvious:

> **The engine can translate pure, statically-typed value math. It cannot translate things that
> reach outside the value graph** — module globals, third-party libraries, dynamic/untyped data
> shapes, reflection, or I/O.

Everything in Part 4 ("what won't accelerate") is a corollary of that sentence.

---

## 3. Strategies that make code fast

### 3.1 Prefer inline expressions for derived features

If a feature is a transformation of other features, write it as an expression instead of a resolver:

```python
# Resolver (runs in Python unless it converts):
@online
def email_domain(email: User.email) -> User.email_domain:
    return email.split("@")[1].lower()

# Expression (always in-engine):
email_domain: str = F.lower(F.split_part(_.email, "@", 2))
```

Common expression building blocks:
- **Conditionals:** `if/else` → `F.if_then_else(cond, a, b)`; null checks → `F.is_null`, `F.coalesce`.
- **JSON extraction:** `json.loads(x)["a"]["b"]` → `F.json_value(x, "$.a.b")`.
- **Casts / strings / math:** `F.cast`, `F.lower`, `F.regexp_like`, arithmetic operators, `F.haversine`, etc.
- **Aggregations over a related collection (has-many):** `_.transactions[_.amount > 100].sum()`,
  `_.events.count()`, `_.candidates[expr].min()`.
- **Windowed aggregations** for time-bucketed rollups.

### 3.2 Keep resolvers pure and narrowly typed

A resolver that *does* convert looks like a math function: typed scalars/structs in, typed
scalars/structs out, no side effects, no globals, no I/O. The more a resolver looks like that, the
more reliably it accelerates. Annotate inputs and outputs precisely — the accelerator reasons about
types, and `Any`/untyped values defeat it.

### 3.3 Parse once into a typed shape, then derive

A classic anti-pattern is re-parsing the same JSON blob in every downstream resolver. Each
`json.loads` is an un-accelerable Python hop. Instead:

1. Parse the blob **once** into a typed structure (a `@dataclass` / typed struct feature).
2. Compute every downstream feature from that typed structure via expressions or pure resolvers.

The single parse may stay Python, but the dozens of consumers become in-engine reads of typed
fields. You collapse N Python hops into 1.

### 3.4 Move reference data into features — never module globals

Lookup tables, geocoded indexes, ML models, "load this dict/CSV at import" — these are the single
most common acceleration killer (see 4.1). The fix is to **model the reference data as a feature
class** the engine can see, then *join* to it instead of dereferencing a Python global.

Example — "distance to nearest location" over a large lookup:

```python
# WON'T accelerate: _LOCATION_INDEX is a module global the engine can't capture,
# and the loop + custom class are opaque.
@online
def nearest_km(lat: F.latitude, lon: F.longitude) -> F.nearest_km:
    return min(haversine(lat, lon, r.lat, r.lon) for r in _LOCATION_INDEX.candidates(lat, lon))

# ACCELERATES: locations become a feature class; cross-join + a built-in distance fn + min().
class Location:
    id: str
    join_key: int          # constant -> cartesian join to every entity
    lat: float
    lon: float

class Entity:
    join_key: int = 1
    locations: DataFrame[Location] = has_many(lambda: Location.join_key == Entity.join_key)
    nearest_km: float = _.locations[
        F.haversine(lat1=_.latitude, lon1=_.longitude, lat2=_.lat, lon2=_.lon, unit="degrees")
    ].min()
```

The data now lives in the engine; the computation is a vectorized join + aggregation. (For very
large batch cross-joins, narrow the join key by a coarse spatial/bucket key instead of a constant.)

### 3.5 Replace network I/O with engine-native HTTP

Blocking calls to external APIs (`requests.post`, etc.) tie up Python worker threads. Engine-native
HTTP (`F.http_post` / `F.http_get` / `F.http_request`) moves the I/O into the engine's async layer. A
clean pattern that keeps the conversion surface tiny:

1. **Build** the request body in a small pure resolver (or expression) → a JSON string feature.
2. **Send** it with an `F.http_*` expression → a typed response feature.
3. **Parse** the response with `F.json_value` (or a single small parse resolver).

Only the build/parse are Python (cheap, no network); the network hop is off the Python path. The two
things to get right: custom TLS trust (internal CAs) and graceful failure handling — a failed call
should yield a null response your parse tolerates, not a hard error.

### 3.6 Decompose mixed resolvers

If one resolver does an accelerable transform *and* one un-accelerable thing, the whole resolver
falls back to Python. **Split it.** The accelerable half converts; only the genuinely-blocked half
stays Python. This is also how you shrink the blast radius of an unavoidable blocker.

### 3.7 Precompute the blocker once, feed it as an input

When a small piece of logic can't accelerate (a flexible date parse, a phone normalization), isolate
it into one tiny resolver whose output becomes an *input feature* to everything downstream. The
downstream resolvers then see a clean typed value and accelerate. You pay the Python cost once
instead of in every consumer. (Even better: if the value can be supplied as a **query input**,
compute it client-side and skip the resolver entirely.)

---

## 4. What won't accelerate — the red flags

When you see these, the answer is usually **restructure (move data to features / split the resolver)
or accept that it stays Python** — not "tweak it until it converts." Recognizing them early saves
the most time.

### 4.1 Module-level globals & runtime-loaded data  ← most common
Dicts, indexes, models, CSVs loaded at import time and referenced inside a resolver. The engine
can't capture a Python global into the value graph (it surfaces as an "unbound name"). **Fix:** model
the data as a feature class and join to it (3.4).

### 4.2 Third-party libraries / C extensions / opaque calls
Phone-number parsers, cloud SDKs, HTTP clients, heavy numeric gymnastics — anything backed by a C
extension or an external service. The accelerator can't symbolically execute a call it can't see
into. **Fix:** replace with an engine-native equivalent if one exists (e.g. `F.http_*` for an HTTP
client), or isolate per 3.6/3.7.

### 4.3 Dynamic / untyped data shapes
Iterating arbitrary dictionary **keys** (`.items()`, `.keys()`, `.values()`), constructing untyped
`dict`s, `dict(some_json)`, or treating a value as "any shape." The engine needs a known, typed
structure. (Iterating a typed **array** is fine; iterating unknown dict keys is not.) Map/dict
column types frequently can't be translated at all. **Fix:** parse into a typed struct (3.3).

### 4.4 Reflection & serialization
`dataclasses.asdict()`, `vars()`, `json.dumps()` of arbitrary objects — anything that turns a typed
value back into a generic bag. These produce loosely-typed output and have no engine equivalent.
**Fix:** read the typed fields directly instead of round-tripping through a dict; keep "export blob"
serialization in a leaf resolver that nothing else depends on.

### 4.5 Flexible datetime parsing & naive datetimes
`strptime` with multiple/ambiguous formats, `hasattr(dt, ...)`, mixing naive and tz-aware datetimes.
Date math over clean typed timestamps is fine; "parse this string that might be one of five formats"
is not. **Fix:** normalize to one typed timestamp once (3.7), or supply it as a query input.

### 4.6 Data-dependent control flow that isn't a fold
Loops/recursion whose shape depends on runtime data in ways that can't be lowered to a
fold/map/filter over a typed collection. Simple accumulation loops over a typed array often *do*
convert; arbitrary `while`-with-early-exit-on-opaque-state does not.

### 4.7 Side effects & I/O
Logging that affects control flow, file/network/db access, randomness, mutable global state. Pure
functions only.

---

## 5. Decide where to invest: accelerate, express, or leave it

Not every un-accelerated resolver is worth fixing. Triage with two questions:

**A. How expensive is it, really?**
- *High value:* loops over large candidate sets, per-row heavy compute, anything on the hot path of a
  latency-sensitive query, repeated JSON re-parsing across many features.
- *Low value:* tiny resolvers that run once and do no I/O (e.g. assembling a request body, a one-off
  cast). These cost microseconds — accelerating them is a checkbox, not a win. **Deprioritize.**

**B. How hard / risky is the fix?**
- *Cheap & safe:* derive-from-typed-value, swap a `json.loads` for `F.json_value`, split a mixed
  resolver. Do these.
- *Structural:* moving reference data into feature classes, reworking a parse chain. Worth it for
  high-value resolvers; validate carefully.
- *Fighting the tool:* trying to coax a third-party-lib / `asdict` / dynamic-dict resolver into
  converting without restructuring. Don't — restructure or leave it.

Then combine: **high value × tractable fix = do it now**; **low value × any difficulty = leave it**;
**high value × structural = plan it and validate parity**.

---

## 6. Parity discipline (non-negotiable for model features)

Acceleration is a **performance** change, not a **behavior** change. When you move logic from Python
to an expression, the output must be **equivalent** — identical for any input that occurs in
practice. For features that feed a model, "close enough" is not enough.

- Run the **same inputs** through the old and new implementations and **diff the outputs** before
  trusting the change.
- Watch for subtle semantic drift: null handling, rounding/precision, float vs int, ordering,
  empty-collection behavior, and edge cases the old code happened to special-case.
- Be skeptical of stale baselines — compare against the *current* old code run *now*, not an old
  captured snapshot, especially when external data or upstream values may have changed.
- Prefer changes that are *provably* equivalent (e.g. a global-min over all rows equals the old
  bounded-radius min for every realistic input) and document why.

A pure restructuring (split a resolver, move data to a feature, parse-once) changes **where** code
runs without changing **what** it computes — that's the lowest-risk category. Rewriting parsing or
scoring logic is higher risk and deserves explicit before/after validation.

---

## 7. Recognizing acceleration without special tooling

If you can't run a static-conversion linter, you can still tell what's happening:

- **Query traces / per-resolver timing:** an accelerated resolver (or an expression) does **not**
  appear as a Python resolver execution in traces. If a resolver shows up as a Python step with
  meaningful wall-time, it didn't accelerate.
- **Latency A/B:** build two versions of a query — one with a suspect feature included, one with it
  (and its dependents) removed — and compare end-to-end latency. The delta is that subtree's Python
  cost. (To remove a feature cleanly, also remove everything that transitively depends on it.)
- **Eyeball the red flags (Part 4):** in practice, scanning a resolver for globals, third-party
  imports, `.items()` / `asdict` / `dict(...)`, and I/O predicts non-acceleration with high accuracy.

---

## 8. Quick reference: Python → expression

| Python in a resolver | Engine-native form |
|---|---|
| `x["a"]["b"]` after `json.loads` | `F.json_value(raw, "$.a.b")` |
| `a if cond else b` | `F.if_then_else(cond, a, b)` |
| `x is None` / `x or default` | `F.is_null(x)` / `F.coalesce(x, default)` |
| `s.lower()`, `s.split(...)` | `F.lower(s)`, `F.split_part(s, sep, n)` |
| `requests.post(url, json=body)` | `F.http_post(url, body=_.body, headers=..., verify=...)` |
| `sum(t.amount for t in items)` | `_.items[_.amount].sum()` |
| `min(dist(p, r) for r in INDEX)` | `_.related[F.haversine(...)].min()` (data as a feature class) |
| `dataclasses.asdict(struct)` | read typed fields directly; avoid round-trip |
| lookup in a module-global dict | join to a reference feature class |

> Function names above are illustrative — confirm exact `F.*` names against your Chalk version's
> function catalog.

---

### TL;DR
Move work into the engine. Prefer expressions; make Python resolvers pure and typed; parse once into
typed shapes; turn reference data and network calls into features / `F.http_*`. Stop pushing when you
hit a global, a third-party lib, a dynamic dict, reflection, or I/O — restructure around it or leave
it. Spend effort only where it's both expensive and tractable, and prove output parity whenever the
feature feeds a model.
