"""DocExtract eval harness.

Offline evaluation framework for the mock LLM extraction pipeline.
Compares /extract output against ground-truth labels, checks cross-field
invariants, detects classification miscalibration, and supports v1 vs v2
model comparison.

Modules:
    normalizers  — date, carrier, amount, and string canonicalization
    comparators  — field-level comparison returning MatchLevel results
    invariants   — cross-field consistency checks (no ground truth required)
    runner       — EvalRunner orchestrates multi-run evaluation; DocumentReport
"""
