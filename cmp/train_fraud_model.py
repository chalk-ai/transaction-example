#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12,<3.14"
# dependencies = ["chalkcompute>=2.1.1"]
# ///
import sys

import chalkcompute

# Train on any dataset shape: every column except `target` is a numeric feature.
DEFAULT_TARGET = "transaction.is_fraud"


@chalkcompute.function(
    secrets=[
        chalkcompute.Secret.from_env("CHALK_CLIENT_ID"),
        chalkcompute.Secret.from_env("CHALK_CLIENT_SECRET"),
        chalkcompute.Secret.from_env("CHALK_ENVIRONMENT_ID"),
    ],
    image=chalkcompute.Image.debian_slim(python_version="3.12").pip_install(
        [
            "chalkpy>=2.130.5",
            "openai",
            "opentelemetry-instrumentation-httpx",
            "xgboost",
            "scikit-learn",
            "pandas",
            "polars",
        ]
    ),
)
def train_fraud_model(dataset: str, target: str) -> None:
    import pandas as pd
    import xgboost as xgb
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import train_test_split

    from chalk.client import ChalkClient
    from chalk.ml import model_handler
    from chalk.scalinggroup import ScalingGroupResourceRequest

    @model_handler
    class FraudModel:
        def predict(self, df):
            # Served as a raw xgboost.Booster, so use the Booster API (no
            # predict_proba). Callers must pass features in the training order.
            X = df.to_pandas().to_numpy(dtype="float32")
            scores = self.model.predict(xgb.DMatrix(X))
            return pd.DataFrame({"fraud_score": scores})

    client = ChalkClient()

    # Every column except the target is a feature.
    df = client.get_dataset(id=dataset).to_pandas(output_id=False, output_ts=False)
    feature_columns = [
        col
        for col in df.columns
        if col not in {target, "transaction.id", "transaction.user.id"}
    ]
    print(f"target: {target}; {len(feature_columns)} features: {feature_columns}")

    X = df[feature_columns].to_numpy(dtype="float32")
    y = df[target].astype(int).to_numpy()

    # Train / test split so we can report a held-out metric.
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=0, stratify=y
    )

    # Train the classifier.
    clf = xgb.XGBClassifier(
        n_estimators=200,
        max_depth=6,
        learning_rate=0.1,
        objective="binary:logistic",
        eval_metric="auc",
    )
    clf.fit(X_train, y_train)

    auc = roc_auc_score(y_test, clf.predict_proba(X_test)[:, 1])
    print(f"held-out AUC: {auc:.4f}")

    # Register the model and roll it out so the online resolver can serve it.
    result = client.register_model_version(
        name="fraud_detection_model",
        model=FraudModel(model=clf),
        input_schema={col: float for col in feature_columns},
        output_schema={"fraud_score": float},
        dependencies=["xgboost", "pandas", "chalkdf"],
        metadata={
            "auc": auc,
        },
    )
    client.deploy_model_version_to_scaling_group(
        name=f"fraud-detection-{result.model_version}",
        model_name="fraud_detection_model",
        model_version=result.model_version,
        resources=ScalingGroupResourceRequest(cpu="1", memory="2Gi"),
    )

    return result.model_version
