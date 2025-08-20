# trunk-ignore-all(pyright/reportInvalidTypeForm,pyright/reportCallIssue,ruff/PLW0603,pyright/reportOptionalMemberAccess)
import os
from typing import TYPE_CHECKING

import lancedb
from chalk.features import (
    DataFrame,
    before_all,
    online,
)
from chalk.logging import chalk_logger
from lancedb.db import DBConnection

from src.transaction_search_result import TransactionSearchResult
from src.models import TransactionSearch

if TYPE_CHECKING:
    from lancedb.table import Table

db: DBConnection | None = None

DB_URI: str = "db://transaction-example-6125m1"
TABLE_NAME: str = "transaction_receipts"  # DODO
VECTOR_COLUMN_NAME: str = "embedding"
REGION: str = "us-east-1"


@before_all
def init_client() -> None:
    global db
    lance_api_key: str | None = os.getenv("lancedb_api_key")
    if lance_api_key is None:
        error_msg: str = "LANCEDB_API_KEY is not set."
        chalk_logger.error(msg=error_msg)
        raise ValueError(error_msg)

    db = lancedb.connect(
        uri=DB_URI,
        api_key=lance_api_key,
        region=REGION,
    )
    chalk_logger.info(
        msg=f"Initializing client: LanceDB",
    )


@online
def get_search_results(
    vector: TransactionSearch.vector,
    limit: TransactionSearch.limit,
    query: TransactionSearch.q,
) -> TransactionSearch.results:
    tbl: Table = db.open_table(
        name=TABLE_NAME,
    )
    results: list = (
        tbl.search(
            query=vector.to_pylist(),
            query_type="vector",
            vector_column_name=VECTOR_COLUMN_NAME,
        )
        .select(
            columns=[
                "id",
                "details",
            ],
        )
        .limit(
            limit=limit,
        )
        .to_list()
    )
    transaction_details: list[TransactionSearchResult] = [
        TransactionSearchResult(
            id=result["id"],
            query=query,
            distance=result["_distance"],
            details=result["details"],
            query_type="VECTOR",
        )
        for result in results
    ]
    return DataFrame(transaction_details)
