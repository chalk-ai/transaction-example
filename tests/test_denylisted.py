from chalk.client import ChalkClient
from src.models import User


def test_denylisted_features(client: ChalkClient):
    client.check(
        input={
            User.id: 1,
            User.email: "elliot@chalk.ai",
        },
        assertions={
            User.denylisted: True,
            User.domain_name: "chalk.ai",
        },
    )
