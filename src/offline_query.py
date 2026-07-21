from chalk.client import ChalkClient

client = ChalkClient()

user_ids = list(range(1000))

dataset = client.offline_query(
    input={"user.id": user_ids},
    query_name='fraud-model-data',
    query_name_version='1.0.0',
    dataset_name='fraud-model',
    recompute_features=True,
    run_asynchronously=True,
    branch="sl-test"
)

df = dataset.to_pandas()
print(df.columns)
print(f"\nDataset shape: {df.shape}")
