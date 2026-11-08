from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.feature_extraction import DictVectorizer
from sklearn.model_selection import KFold


ANOMALY_THRESHOLD_PERCENTILE = 97.5
MODEL_SEED = 42


@dataclass
class HistoricalAnomalyModel:
    vectorizer: DictVectorizer
    estimator: IsolationForest
    reference_scores: np.ndarray
    training_rows: int
    excluded_labeled_rows: int

    def score(self, features: list[dict[str, Any]]) -> list[dict[str, Any]]:
        matrix = self.vectorizer.transform(features)
        anomaly_scores = -self.estimator.score_samples(matrix)
        return [
            {
                "percentile": round(
                    float(np.mean(self.reference_scores <= score) * 100), 1
                ),
                "isOutlier": bool(
                    np.mean(self.reference_scores <= score)
                    >= ANOMALY_THRESHOLD_PERCENTILE / 100
                ),
                "thresholdPercentile": ANOMALY_THRESHOLD_PERCENTILE,
            }
            for score in anomaly_scores
        ]

    def metadata(self, scored_applications: int, outlier_applications: int) -> dict[str, Any]:
        return {
            "algorithm": "Isolation Forest",
            "trainingMode": "Unsupervised novelty detection",
            "trainingRows": self.training_rows,
            "excludedKnownRuleRows": self.excluded_labeled_rows,
            "scoredApplications": scored_applications,
            "outlierApplications": outlier_applications,
            "thresholdPercentile": ANOMALY_THRESHOLD_PERCENTILE,
            "features": [
                "Loan amount and amount-to-revenue ratio",
                "Annual revenue and business age",
                "Loan term, industry, product, purpose, and bank",
                "Deposit volume, count, round-dollar share, and concentration",
            ],
            "limitations": (
                "Trained on historical deals without an existing bank-rule label. "
                "Labels were excluded from features. The displayed percentile is an "
                "anomaly rank, not a fraud probability or a validated performance metric. "
                "Graph and transaction-sequence features are not included because historical "
                "transaction-level relationship data and validated outcomes are unavailable "
                "for reliable training; those signals remain separately interpretable."
            ),
        }


def _new_estimator() -> IsolationForest:
    return IsolationForest(
        n_estimators=200,
        max_samples="auto",
        contamination="auto",
        random_state=MODEL_SEED,
        n_jobs=1,
    )


def train_historical_anomaly_model(
    rows: list[dict[str, Any]],
) -> HistoricalAnomalyModel:
    baseline = [
        row for row in rows
        if (row.get("fraud_label") or "none").casefold() == "none"
    ]
    excluded_labeled_rows = len(rows) - len(baseline)
    if len(baseline) < 20:
        raise ValueError(
            "At least 20 historical deals without existing bank-rule labels "
            "are required to train the anomaly model."
        )

    feature_rows = [row["features"] for row in baseline]
    cross_fitted_scores = np.empty(len(feature_rows), dtype=float)
    folds = KFold(n_splits=5, shuffle=True, random_state=MODEL_SEED)
    for training_indices, held_out_indices in folds.split(feature_rows):
        fold_vectorizer = DictVectorizer(sparse=False)
        training_matrix = fold_vectorizer.fit_transform(
            [feature_rows[index] for index in training_indices]
        )
        held_out_matrix = fold_vectorizer.transform(
            [feature_rows[index] for index in held_out_indices]
        )
        fold_estimator = _new_estimator()
        fold_estimator.fit(training_matrix)
        cross_fitted_scores[held_out_indices] = -fold_estimator.score_samples(
            held_out_matrix
        )

    vectorizer = DictVectorizer(sparse=False)
    matrix = vectorizer.fit_transform(feature_rows)
    estimator = _new_estimator()
    estimator.fit(matrix)
    return HistoricalAnomalyModel(
        vectorizer=vectorizer,
        estimator=estimator,
        reference_scores=cross_fitted_scores,
        training_rows=len(baseline),
        excluded_labeled_rows=excluded_labeled_rows,
    )
