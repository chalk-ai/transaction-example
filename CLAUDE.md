
# Chalk Development Guidelines

## Project Skills — READ THESE FIRST

| Skill | Read it when you are… |
|---|---|
| `chalk-notebooks` | creating, running, or debugging a Chalk notebook — `chalk notebook create` / `cell add` / `cell edit` / `run`, or working in a dashboard notebook; running queries in a cell, `client.load_features()`, plotting, `uv` installs, training a model in the kernel |
| `writing-online-resolvers` | writing, refactoring, or debugging `@online` Python resolvers — input/output signatures, `Now`, dotted relationship paths, Python resolver vs. expression, null handling, tests |
| `writing-static-chalkdf` | writing `@online(static=True)` resolvers with `chalkdf` — porting pandas/numpy logic, exploding has-many, window functions, `join_asof`, sharding, `chalk apply` serialization errors |
| `chalk-streaming` | writing, testing, or tuning stream resolvers — Kafka/Kinesis/PubSub, `make_stream_resolver`, parse expressions, sinks, dedup, late arrival, streaming worker config |
| `chalk-resolver-acceleration` | optimizing feature-pipeline latency — whether to accelerate a Python resolver, migrate it to inline `F.*` / `_.` expressions, or restructure code that will never statically accelerate |
| `migrating-features-to-chalk` | porting existing features into Chalk — dbt models, Airflow/Prefect pandas jobs, Spark jobs, Feast/Tecton; entity modeling, resolver choice, train-serve parity |

**"Use your skills" in a prompt means these**, not just the harness-provided ones.

## Chalk Documentation

For complete Chalk documentation optimized for AI assistants:

- **Quick reference**: https://docs.chalk.ai/llms.txt (~200KB, core concepts)
- **Full documentation**: https://docs.chalk.ai/llms-full.txt (~800KB, comprehensive)

Fetch these URLs when you need detailed API references, examples, or information beyond what's included above.

This document provides comprehensive guidelines for writing correct Chalk code based on the official Chalk documentation.

**For additional help:** Use https://docs.chalk.ai or review examples at https://github.com/chalk-ai/examples to learn.

## What is Chalk?

Chalk is a programmable feature engine that powers low-latency inference, rapid model iteration, and observability across the ML lifecycle. It eliminates core pain points in enterprise AI/ML systems by providing:

- End-to-end platform for deploying and scaling enterprise-grade infrastructure
- Feature pipelines authored in pure Python (no DSLs)
- High throughput batch offline queries with point-in-time correctness
- Unstructured data integration with LLMs
- Fresh features on-the-fly with versioning, branching, and full observability
- Unified feature and prompt engineering, LLM evals, and real-time inference

## Core Architecture

### Feature Classes
Feature classes are the foundation of Chalk. They define the structure and types of your data using Python dataclass-like syntax.

```python
from chalk.features import features, Primary
from datetime import datetime

@features
class User:
    id: int  # Primary key (implicit when named 'id')
    name: str
    email: str
    birthday: datetime
    fraud_score: float
```

**Key Points:**
- Use `@features` decorator on Python classes
- Features are namespaced by their containing class (e.g., `user.name`, `user.email`)
- Primary keys default to `id` field, or use `Primary[type]` for explicit keys
- Support standard Python types: `int`, `str`, `float`, `bool`, `datetime`, `Optional[T]`, `list[T]`

### Relationships

#### Has-One Relationships (One-to-One)
```python
@features
class Profile:
    id: str
    user_id: "User.id"  # Foreign key reference
    email_age_years: float

@features
class User:
    id: str
    profile: Profile  # Implicit join via user_id
```

#### Has-Many Relationships (One-to-Many)
```python
from chalk.features import DataFrame

@features
class Transaction:
    id: str
    user_id: "User.id"  # Foreign key reference
    amount: float
    created_at: datetime

@features
class User:
    id: str
    transactions: DataFrame[Transaction]  # Implicit join
```

**Best Practices:**
- Use implicit joins via foreign key annotations (`user_id: "User.id"`)
- Prefer implicit syntax over explicit `has_one()` and `has_many()` functions
- Use forward references with quotes for classes defined later
- Support composite join keys with multiple fields

## Resolvers

Resolvers define HOW feature values are computed. There are three types:

### 1. SQL Resolvers (Recommended for Data Loading)

**SQL File Resolvers (.chalk.sql files):**
```sql
-- source: pg_users
-- resolves: User
-- type: online
select id, name, email, birthday from users
```

**Generated SQL Resolvers:**
```python
from chalk import make_sql_file_resolver

make_sql_file_resolver(
    name="get_user",
    source="pg_users",
    resolves="User",
    query="select id, name, email, birthday from users"
)
```

### 2. Python Resolvers

```python
from chalk import online, offline

@online
def get_email_domain(email: User.email) -> User.email_domain:
    return email.split('@')[1].lower()

@offline
def batch_credit_scores() -> DataFrame[User.id, User.credit_score]:
    # Process large batches of data
    return DataFrame.read_csv("credit_scores.csv")
```

**Key Rules:**
- All resolver inputs must be from the same root namespace
- Use type annotations to specify input/output features
- Return single values, feature class instances, or DataFrames
- Use `Features[...]` for multiple outputs
- Use `@online` for real-time inference, `@offline` for batch processing

**Static Resolvers (for reference data):**
```python
from chalk.features import DataFrame, online, _

@online(static=True)
def get_reference_data() -> DataFrame[
    Feature.id,
    Feature.value,
]:
    from chalkdf import DataFrame

    return DataFrame.scan(
        ["s3://bucket/path/to/data/*.parquet"]
    ).select(
        _.id.alias("feature.id"),
        _.value.alias("feature.value"),
    )
```

Static resolvers (`static=True`) run once at deployment startup to generate a plan: ideal for loading reference data from S3 or cloud storage. Use `chalkdf.DataFrame` for scanning parquet files.

### 3. Streaming Resolvers

For real-time data ingestion from Kafka or other streaming sources:

```python
from chalk.features.resolver import make_stream_resolver
from chalk.streams import KafkaSource
from chalk.features import _
import chalk.functions as F
from pydantic import BaseModel

transactions_kafka = KafkaSource(name="transactions_topic")

class TransactionMessage(BaseModel):
    id: str
    timestamp: int
    user_id: int
    amount: float

transaction_stream = make_stream_resolver(
    name="get_transactions_stream",
    source=transactions_kafka,
    message_type=TransactionMessage,
    output_features={
        Transaction.id: _.id,
        Transaction.timestamp: F.from_unix_seconds(_.timestamp / 1000),
        Transaction.user_id: _.user_id,
        Transaction.amount: _.amount,
    },
)
```

**Key Points:**
- Use `make_stream_resolver` for native streaming ingestion
- Define message schema with Pydantic `BaseModel`
- Map message fields to features using `output_features` dictionary
- Use `chalk.functions` (e.g., `F.from_unix_seconds`) for transformations

**Parsing Avro messages:**
```python
from chalk.features.resolver import make_stream_resolver
from chalk.streams import KafkaSource
from chalk.features import _
import chalk.functions as F
from pydantic import BaseModel

events_kafka = KafkaSource(name="events_topic")

AVRO_SCHEMA = """
{
  "type": "record",
  "name": "EventMessage",
  "fields": [
    {"name": "timestamp", "type": "long"},
    {"name": "user_id", "type": "int"},
    {"name": "event_type", "type": "string"}
  ]
}
"""

class EventMessage(BaseModel):
    timestamp: int
    user_id: int
    event_type: str

# Parse raw bytes to structured message
parsed_message = F.avro_deserialize(_, schema=AVRO_SCHEMA, target_type=EventMessage)

# Handle parse failures gracefully with F.recover
safe_parsed = F.recover(parsed_message, None)

event_stream = make_stream_resolver(
    name="get_events_stream",
    source=events_kafka,
    parse=safe_parsed,
    message_type=EventMessage,
    output_features={
        Event.id: F.cast(_.user_id, str) + "_" + F.cast(_.timestamp, str),
        Event.user_id: _.user_id,
        Event.timestamp: F.from_unix_seconds(_.timestamp),
        Event.event_type: _.event_type,
    },
)
```

**Parsing Protobuf messages:**
```python
from messages.user_pb2 import UserMessage  # Import generated protobuf classes
from chalk.features.resolver import make_stream_resolver
from chalk.features import _
import chalk.functions as F

# Strip first 6 bytes (e.g., header), then parse the protobuf message
parsed_message = F.proto_deserialize(F.substr(_, 6, None), UserMessage)

# Exclude any UserMessages whose user_type == GUEST
parse_expression = F.if_then_else(
    parsed_message.user_type == UserMessage.UserType.GUEST,
    None,
    parsed_message,
)

users_streaming_resolver = make_stream_resolver(
    name="get_users_stream",
    source=kafka_source,
    message_type=UserMessage,
    parse=parse_expression,
    output_features={
        User.user_id: _.id,
        User.full_name: _.name,
        User.email_address: _.contact_info.email_address,  # Nested field access
        User.updated_at: F.from_unix_seconds(_.updated_at / 1_000_000),
    },
)
```

### 4. Expressions (High-Performance Inline Features)

```python
import chalk.functions as F
from chalk import _

@features
class Transaction:
    id: int
    amount: float
    discount_percentage: float

    # Inline expressions compiled to C++
    is_expensive: bool = _.amount > 100
    net_amount: float = _.amount * (1 - _.discount_percentage / 100)

@features
class User:
    id: int
    name: str
    email: str
    transactions: DataFrame[Transaction]

    # String similarity using Chalk functions
    name_email_match: float = F.levenshtein_distance(_.name, _.email)

    # DataFrame aggregations
    transaction_count: int = _.transactions.count()
    total_spent: float = _.transactions[_.amount].sum()
    large_transactions: int = _.transactions[_.amount > 1000].count()
```

**Expression Features:**
- Use `_` to reference current scope (feature class)
- Support arithmetic, boolean, and comparison operators
- Built-in functions in `chalk.functions` module
- DataFrame filtering, projections, and aggregations
- Compiled to optimized C++ for low-latency execution

## DataFrame Operations

Chalk DataFrames support sophisticated operations:

### Filtering and Projections
```python
# Filter transactions over $100
large_txns = _.transactions[_.amount > 100]

# Project specific columns
amounts = _.transactions[_.amount, _.created_at]

# Combined filtering and projection
recent_large = _.transactions[
    _.amount > 100,
    _.created_at > datetime(2024, 1, 1),
    _.amount, _.memo
]
```

### Aggregations
```python
# Basic aggregations
count: int = _.transactions.count()
total: float = _.transactions[_.amount].sum()
avg_amount: float = _.transactions[_.amount].mean()
max_amount: float = _.transactions[_.amount].max()
min_amount: float = _.transactions[_.amount].min()

# Conditional aggregations
large_count: int = _.transactions[_.amount > 1000].count()
```

## Windowed Aggregations

For time-based features:

```python
from chalk import windowed, Windowed

@features
class User:
    id: int
    transactions: DataFrame[Transaction]

    # Multiple time windows with proper filtering
    transaction_amounts: Windowed[float] = windowed(
        "1d", "7d", "30d",
        expression=_.transactions[
            _.amount,
            _.created_at > _.chalk_window,  # Start of window
            _.created_at < _.chalk_now,      # Current time
        ].sum(),
        default=0,
    )
```

**Understanding `_.chalk_window` and `_.chalk_now`:**
- `_.chalk_window` - The start of the time window (dynamically computed based on window size)
- `_.chalk_now` - The current timestamp at query time

**Important:** For windowed aggregations, use `_.chalk_window` instead of manual timedelta offsets:
```python
# CORRECT - use _.chalk_window for window-relative filtering
_.created_at > _.chalk_window
_.created_at < _.chalk_now

# ALSO VALID - timedelta offset from now (for explicit offsets outside windowed context)
_.created_at > _.chalk_now - timedelta(days=7)

# BUT PREFER _.chalk_window in windowed() expressions - it automatically
# adjusts to each window size ("1d", "7d", "30d") you define
```

**With materialization for high-volume data:**
```python
total_spent: Windowed[float] = windowed(
    "1d", "7d", "30d",
    expression=_.transactions[
        _.amount,
        _.created_at > _.chalk_window,
        _.created_at < _.chalk_now,
    ].sum(),
    materialization={
        "bucket_durations": {
            "1h": ["1d"],
            "1d": ["7d", "30d"],
        },
    },
    default=0,
)
```

## Data Source Integration

### Configure Data Sources
1. Define data sources in Chalk dashboard
2. Test connections using "Test Data Source" button
3. Use descriptive names (not generic types like "postgres")

### SQL Resolver Configuration
```sql
-- Required comments
-- source: my_postgres_db
-- resolves: User

-- Optional configurations
-- type: online|offline|streaming
-- count: 1|one|one_or_none|all
-- timeout: 5m
-- cron: 0 0 * * *
-- owner: engineer@company.com
-- tags: ['user', 'profile']
-- environment: 'production'

select id, name, email from users where id = $\{user.id}
```

### Incremental Queries

For efficient ingestion of immutable/append-only tables (event logs, transactions):

```sql
-- type: offline
-- resolves: LoginEvent
-- source: PG
-- incremental:
--   mode: row
--   lookback_period: 60m
--   incremental_column: attempted_at
select attempted_at, status, user_id from logins
```

**How it works:**
- First run ingests all data from the source
- Subsequent runs only fetch new rows based on `incremental_column`
- The `incremental_column` must be monotonically increasing (typically a timestamp)
- `lookback_period` handles late-arriving data by re-checking recent rows

**Use cases:** Event tables, audit logs, transaction history - any immutable data that grows over time.

## Best Practices

### Feature Design
1. **Start with feature definitions** - Define all feature classes before resolvers
2. **Use separate files** - Keep feature definitions separate from resolvers
3. **Single file initially** - Define all features in one file to avoid circular dependencies
4. **Tag and annotate** features for documentation and monitoring:
```python
@features(owner="team@company.com", tags=['group:risk'])
class User:
    id: str
    # the user's full name
    # :owner: mary.shelley@company.com
    # :tags: team:identity, priority:high
    name: str
```

5. **Forward references** - When referencing a feature class that's defined later or in another file, put quotes around the entire type annotation:
```python
# CORRECT - quotes around the whole type
reservations: "DataFrame[Reservation]" = has_many(
    lambda: Reservation.user_id == User.id
)

# WRONG - quotes only around class name inside brackets
reservations: DataFrame["Reservation"] = has_many(...)  # Will cause issues!
```

6. **Circular imports** - When features reference each other across files, use `TYPE_CHECKING`:
```python
from typing import TYPE_CHECKING
from chalk.features import features, has_many, DataFrame

if TYPE_CHECKING:
    from src.models.user import User

@features
class Transaction:
    id: str
    user_id: "User.id"  # String reference for foreign key
    amount: float

# For has_many with lambdas referencing other classes
@features
class User:
    id: str
    transactions: "DataFrame[Transaction]" = has_many(
        lambda: User.id == Transaction.user_id
    )
```

### Resolver Patterns
1. **Use SQL for data loading** - SQL resolvers are more efficient than Python for raw data
2. **Explicit column selection** - Avoid `SELECT *` in SQL resolvers
3. **Clear naming** - Give resolvers descriptive names
4. **Single feature space** - Resolver inputs/outputs must belong to same feature class
5. **Transform to Pandas/Polars** - Conversion from Chalk DataFrame is nearly free

### Performance Optimization
1. **Use expressions over Python resolvers** when possible for low-latency features
2. **Materialized aggregations** for high-volume time-window computations
3. **Caching with TTLs** for expensive computations:
```python
expensive_feature: str = feature(
    max_staleness="30d",  # Cache for 30 days
    expression=some_complex_computation()
)
```

### Deployment & Testing

1. **Lint before deploying** - Always check for errors before deployment:
```bash
chalk lint          # Check for errors
chalk apply         # Deploy after lint passes
```

2. **Use branch deployments** for testing:
```bash
chalk apply --branch feature-branch
chalk query --branch feature-branch --in user.id=123
```

3. **Named queries** for complex, reusable query patterns:
```python
from chalk import NamedQuery

NamedQuery(
    name="fraud_detection",
    input=[User.id],
    output=[User.fraud_score, User.risk_flags],
    staleness={User.risk_flags: "1h"},
    tags=["fraud", "security"]
)
```

4. **Unit testing expressions** with `check_expression`:
```python
from chalk.testing import check_expression

def test_is_successful():
    check_expression(
        Order.is_successful,
        [
            Order(id="1", status="succeeded", is_successful=True),
            Order(id="2", status="failed", is_successful=False),
        ],
    )
```

5. **Testing with has_many features** - Pass related entities as lists:
```python
from datetime import datetime, timezone
from chalk.client import ChalkClient

def test_user_total_spent(chalk_client: ChalkClient):
    chalk_client.check(
        input={
            User.id: 1,
            User.transactions_sent: [
                Transaction(id="t1", amount=5000, timestamp=datetime(2025, 12, 4, 11, 0)),
                Transaction(id="t2", amount=3000, timestamp=datetime(2025, 12, 4, 12, 0)),
            ],
        },
        assertions={
            User.total_amount_sent["7d"]: 8000.0
        },
        now=datetime(2025, 12, 5, tzinfo=timezone.utc),
    )
```

6. **Testing streaming resolvers**:
```python
def test_transactions_stream(chalk_client: ChalkClient):
    sample_messages = [...]  # Your test message payloads

    result = chalk_client.test_streaming_resolver(
        resolver=transaction_streaming_resolver,
        message_bodies=sample_messages,
    )
    assert len(result.features) > 0
```

## Common Patterns

### API Integration
```python
import requests
from chalk import online

@online
def get_credit_score(user_id: User.id) -> User.credit_score:
    response = requests.get(f"https://api.credit.com/score/{user_id}")
    return response.json()["score"]
```

### Model Registry

Chalk's model registry provides versioned model management with automatic deployment:

**Loading models into deployment:**
```python
from chalk.features import feature, features
from chalk.ml import ModelReference
from chalk import make_model_resolver

# Load a model by alias (e.g., "latest", "production")
risk_model = ModelReference.from_alias(
    name="RiskScoreModel",
    alias="latest",
)

# Or load by specific version number
risk_model_v1 = ModelReference.from_version(
    name="RiskScoreModel",
    version=1,
)

@features
class User:
    id: int
    age: int
    income: float
    risk_score: float = feature(versions=2)  # Versioned feature

# Create model resolver connecting model to features
make_model_resolver(
    name="user_risk_score_prediction",
    model=risk_model,
    input=[User.age, User.income],
    output=[User.risk_score],
)

# For versioned features, use @ syntax
make_model_resolver(
    name="user_risk_score_v2_prediction",
    model=risk_model,
    input=[User.age, User.income],
    output=[User.risk_score@2],
)
```

**Registering models (run separately, not in deployment code):**
```python
from chalk.client import ChalkClient

client = ChalkClient()

# Option 1: Register a trained Python model object
from sklearn.ensemble import RandomForestClassifier
rfc = RandomForestClassifier()
rfc.fit(X_train, y_train)

client.register_model_version(
    name="RiskScoreModel",
    aliases=["v1.0", "latest"],
    model=rfc,
    input_features=[User.age, User.income],
    output_features=[User.risk_score],
)

# Option 2: Register from local model files
client.register_model_version(
    name="RiskScoreModel",
    aliases=["v2.0"],
    model_paths=["./model.pth"],
    additional_files=["./tokenizer.json"],
    metadata={"framework": "pytorch"},
)
```

### LLM Integration
```python
import chalk.prompts as P
from chalk.features import features

@features
class Document:
    id: str
    content: str
    summary: str = P.completion(
        model="gpt-4o-mini",
        messages=[P.message(
            role="user",
            content="Summarize this document: {{Document.content}}"
        )]
    )
```

## Query Patterns

### Online Queries (Real-time)
```python
from chalk.client import ChalkClient

client = ChalkClient()
result = client.query(
    input={User.id: "123"},
    output=[User.name, User.fraud_score, User.transactions],
    staleness={User.fraud_score: "1h"}  # Accept cached values up to 1 hour old
)
```

### Offline Queries (Batch)
```python
dataset = client.offline_query(
    input={User.id: list(range(1000))},
    output=[User.fraud_score, User.transaction_count],
    dataset_name="fraud_training_data",
    recompute_features=True  # Force recomputation for point-in-time correctness
)
df = dataset.to_pandas()
```

## Error Handling

### Feature Defaults
```python
@features
class User:
    transaction_count: int = 0  # Default value
    risk_score: float = feature(default=0.5, max_staleness="1d")
```

### Optional Relationships
```python
@features
class User:
    id: str
    profile: "Profile | None"  # Optional relationship

@online
def compute_score(profile: User.profile) -> User.profile_score:
    if profile is None:
        return 0.0
    return profile.completeness_score
```

## Version Control & Environments

### Feature Versioning
```python
@features
class User:
    risk_score: float = feature(version=2)  # Explicit version
```

### Environment-Specific Features
```python
risk_score: float = feature(
    expression=compute_risk(),
    environment=["staging", "dev"],
    tags=["fraud", "experimental"]
)
```

### Branch-based Development
```bash
# Deploy to branch
chalk apply --branch my-feature

# Query branch
chalk query --branch my-feature --in user.id=123

# Promote to production
chalk apply
```

## Security & Access Control

1. **Use access tokens** with appropriate scopes
2. **Never log or commit secrets** in resolvers
3. **Environment variables** for configuration:
```python
import os

@online
def call_external_api():
    api_key = os.environ["EXTERNAL_API_KEY"]
    # Use api_key securely
```

## Monitoring & Observability

### Feature Monitoring
```python
from chalk.features import feature

critical_feature: float = feature(
    expression=compute_critical_value(),
    owner="team@company.com",
    tags=["critical", "monitored"],
    # Add validations for data quality
)
```

### Logging and Tracing
- All feature computations are automatically traced
- Use Chalk dashboard for monitoring feature drift and performance
- Set up alerts on critical features and resolvers

This guide covers the essential patterns and best practices for writing effective Chalk code. Always refer to the official Chalk documentation for the most current API details and advanced features.
