"""Evaluate replaceable FRA model outputs against human-labelled JSON samples."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean
from typing import Any

from app.services.evaluation import evaluate_ocr_text


TASKS = {"ocr", "entity_extraction", "asset_classification", "asset_detection"}


def _score(tp: int, fp: int, fn: int) -> dict[str, float | int]:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "support": tp + fn,
    }


def _text(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("text", "")
    return str(value or "")


def _ocr_report(labels: list, predictions: list) -> dict:
    rows = [evaluate_ocr_text(_text(label), _text(prediction)) for label, prediction in zip(labels, predictions)]
    return {
        "character_error_rate": round(mean(row.character_error_rate for row in rows), 4),
        "word_error_rate": round(mean(row.word_error_rate for row in rows), 4),
    }


def _entity_report(labels: list, predictions: list) -> dict:
    keys = sorted({key for item in labels + predictions if isinstance(item, dict) for key in item})
    scores = {}
    for key in keys:
        tp = fp = fn = 0
        for expected, actual in zip(labels, predictions):
            expected_value = expected.get(key) if isinstance(expected, dict) else None
            actual_value = actual.get(key) if isinstance(actual, dict) else None
            if expected_value is not None and actual_value == expected_value:
                tp += 1
            else:
                if actual_value is not None:
                    fp += 1
                if expected_value is not None:
                    fn += 1
        scores[key] = _score(tp, fp, fn)
    macro = {
        metric: round(mean(value[metric] for value in scores.values()), 4) if scores else 0.0
        for metric in ("precision", "recall", "f1")
    }
    return {"per_label": scores, "macro": macro}


def _asset_label(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("label") or value.get("asset_class") or "")
    return str(value or "")


def _asset_report(labels: list, predictions: list) -> dict:
    expected = [_asset_label(item) for item in labels]
    actual = [_asset_label(item) for item in predictions]
    classes = sorted(set(expected + actual) - {""})
    per_class = {}
    for name in classes:
        tp = sum(e == name and a == name for e, a in zip(expected, actual))
        fp = sum(e != name and a == name for e, a in zip(expected, actual))
        fn = sum(e == name and a != name for e, a in zip(expected, actual))
        per_class[name] = _score(tp, fp, fn)
    macro = {
        metric: round(mean(value[metric] for value in per_class.values()), 4) if per_class else 0.0
        for metric in ("precision", "recall", "f1")
    }
    report: dict[str, Any] = {"per_class": per_class, "macro": macro}
    ious = [float(item["iou"]) for item in predictions if isinstance(item, dict) and item.get("iou") is not None]
    if ious:
        if any(value < 0 or value > 1 for value in ious):
            raise ValueError("IoU values must be between zero and one.")
        report["mean_iou"] = round(mean(ious), 4)
    return report


def evaluation_report(labels: list, predictions: list, *, task: str) -> dict:
    """Return transparent metrics; an empty labelled set is explicitly unevaluated."""
    if task not in TASKS:
        raise ValueError(f"Unsupported task: {task}.")
    if not isinstance(labels, list) or not isinstance(predictions, list):
        raise ValueError("Labels and predictions must be JSON arrays.")
    if not labels:
        return {"task": task, "status": "not_evaluated", "sample_count": 0}
    if len(labels) != len(predictions):
        raise ValueError("Labels and predictions must contain the same number of samples.")
    if task == "ocr":
        metrics = _ocr_report(labels, predictions)
    elif task == "entity_extraction":
        metrics = _entity_report(labels, predictions)
    else:
        metrics = _asset_report(labels, predictions)
    return {"task": task, "status": "evaluated", "sample_count": len(labels), **metrics}


def _load(path: Path) -> list:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list):
        raise ValueError(f"{path} must contain a JSON array.")
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--task", choices=sorted(TASKS), required=True)
    args = parser.parse_args()
    print(json.dumps(evaluation_report(_load(args.labels), _load(args.predictions), task=args.task), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
