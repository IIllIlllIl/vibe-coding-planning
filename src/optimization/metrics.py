"""Binary classification metrics for candidate reports."""

from __future__ import annotations

import math
from typing import Iterable


def classification_metrics(
    labels: Iterable[bool],
    predictions: Iterable[bool],
) -> dict[str, float | int]:
    pairs = list(zip(labels, predictions, strict=True))
    tp = sum(label and pred for label, pred in pairs)
    tn = sum(not label and not pred for label, pred in pairs)
    fp = sum(not label and pred for label, pred in pairs)
    fn = sum(label and not pred for label, pred in pairs)
    total = len(pairs)

    def ratio(num: float, den: float) -> float:
        return num / den if den else 0.0

    precision = ratio(tp, tp + fp)
    recall = ratio(tp, tp + fn)
    specificity = ratio(tn, tn + fp)
    mcc_den = math.sqrt((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn))
    return {
        "tp": tp,
        "tn": tn,
        "fp": fp,
        "fn": fn,
        "accuracy": ratio(tp + tn, total),
        "balanced_accuracy": (recall + specificity) / 2,
        "precision": precision,
        "recall": recall,
        "f1": ratio(2 * precision * recall, precision + recall),
        "mcc": ratio(tp * tn - fp * fn, mcc_den),
        "pass_rate": ratio(tp + fp, total),
    }
