#!/usr/bin/env python3
"""
Create train/val/test splits for the benchmark report list.
"""

from __future__ import annotations

import argparse
import csv
import random
from pathlib import Path
from typing import List


def _parse_splits(total: int, train: float, val: float, test: float) -> tuple[int, int, int]:
    if train <= 1 and val <= 1 and test <= 1:
        train_n = int(round(total * train))
        val_n = int(round(total * val))
        test_n = total - train_n - val_n
    else:
        train_n = int(train)
        val_n = int(val)
        test_n = int(test)
    if train_n + val_n + test_n != total:
        diff = total - (train_n + val_n + test_n)
        train_n += diff
    return train_n, val_n, test_n


def main() -> None:
    parser = argparse.ArgumentParser(description="Split benchmark CSV into train/val/test.")
    parser.add_argument("--csv", default="benchmarks/reports_2024_2025.csv", help="Input CSV")
    parser.add_argument("--out", default="benchmarks/reports_2024_2025_split.csv", help="Output CSV")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--train", type=float, default=0.67)
    parser.add_argument("--val", type=float, default=0.17)
    parser.add_argument("--test", type=float, default=0.16)
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        raise SystemExit(f"CSV not found: {csv_path}")

    with open(csv_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    rng = random.Random(args.seed)
    rng.shuffle(rows)

    train_n, val_n, test_n = _parse_splits(len(rows), args.train, args.val, args.test)
    splits: List[str] = (
        ["train"] * train_n + ["val"] * val_n + ["test"] * test_n
    )

    for row, split in zip(rows, splits):
        row["split"] = split

    with open(args.out, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {args.out} (train={train_n}, val={val_n}, test={test_n})")


if __name__ == "__main__":
    main()
