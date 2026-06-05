"""Inter-rater agreement (Cohen's kappa, Gwet's AC1) on the reviewer overlap."""
from __future__ import annotations

import csv
from pathlib import Path

from dark_health_data.evaluation import agreement


def _write(path: Path, pairs: list[tuple[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["row_uid", "correct"])
        w.writerows(pairs)


def test_perfect_agreement(tmp_path: Path) -> None:
    rows = [("r1", "1"), ("r2", "1"), ("r3", "0"), ("r4", "0")]
    _write(tmp_path / "a.csv", rows)
    _write(tmp_path / "b.csv", rows)
    r = agreement(tmp_path / "a.csv", tmp_path / "b.csv")
    assert r["n_overlap"] == 4
    assert r["percent_agreement"] == 1.0
    assert r["cohen_kappa"] == 1.0
    assert r["gwet_ac1"] == 1.0
    assert r["disagreement_row_uids"] == []


def test_partial_agreement_hand_computed(tmp_path: Path) -> None:
    # A=[1,1,0,0], B=[1,0,0,0] -> p_o=0.75; kappa=0.5; AC1=0.5294 (hand-derived)
    _write(tmp_path / "a.csv", [("r1", "1"), ("r2", "1"), ("r3", "0"), ("r4", "0")])
    _write(tmp_path / "b.csv", [("r1", "1"), ("r2", "0"), ("r3", "0"), ("r4", "0")])
    r = agreement(tmp_path / "a.csv", tmp_path / "b.csv")
    assert r["n_overlap"] == 4
    assert r["percent_agreement"] == 0.75
    assert r["cohen_kappa"] == 0.5
    assert r["gwet_ac1"] == 0.5294
    assert r["disagreement_row_uids"] == ["r2"]
    assert r["contingency"]["A_correct_B_incorrect"] == 1


def test_only_overlap_counts(tmp_path: Path) -> None:
    # rows unique to one reviewer are ignored; only shared row_uids compared
    _write(tmp_path / "a.csv", [("r1", "1"), ("r2", "1"), ("solo_a", "0")])
    _write(tmp_path / "b.csv", [("r1", "1"), ("r2", "1"), ("solo_b", "1")])
    r = agreement(tmp_path / "a.csv", tmp_path / "b.csv")
    assert r["n_overlap"] == 2
    assert r["percent_agreement"] == 1.0
