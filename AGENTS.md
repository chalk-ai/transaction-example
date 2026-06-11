
# Chalk Development Guidelines

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

### 3. Expressions (High-Performance Inline Features)

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

    # Multiple time windows
    transaction_amounts: Windowed[float] = windowed(
        "1d", "7d", "30d",
        expression=_.transactions[
            _.amount,
            _.created_at > _.chalk_window
        ].sum()
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
1. **Use branch deployments** for testing:
```bash
chalk apply --branch feature-branch
chalk query --branch feature-branch --in user.id=123
```

2. **Named queries** for complex, reusable query patterns:
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

3. **Unit testing** with pytest:
```python
def test_email_domain_resolver():
    result = get_email_domain("user@example.com")
    assert result == "example.com"
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

### ML Model Integration
```python
import mlflow
from chalk import online
from chalk.features import before_all

model = None

@before_all
def load_model():
    global model
    model = mlflow.load_model("models:/fraud-detection@production")

@online
def predict_fraud(features: User) -> User.fraud_probability:
    return model.predict([[features.age, features.transaction_count]])[0]
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
