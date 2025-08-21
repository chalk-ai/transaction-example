from chalk.features import (
    Primary,
    features,
)


@features
class TransactionSearchResult:
    # from vector database
    id: Primary[str]
    query: str
    distance: float | None
    query_type: str
    body: str
