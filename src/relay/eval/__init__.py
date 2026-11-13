"""Layer 1 & Layer 5 evaluation harness, grading, and deterministic table recomputation."""

from .grader import EvalQuestion, grade_answer
from .runner import ScenarioSpec, load_questions, run_load_scenario, run_retrieval_eval
from .tables import (
    analyze_slow_trace,
    compute_retrieval_table,
    compute_scenario_tables,
    load_results_csv,
    load_traces_jsonl,
)

__all__ = [
    "EvalQuestion",
    "ScenarioSpec",
    "analyze_slow_trace",
    "compute_retrieval_table",
    "compute_scenario_tables",
    "grade_answer",
    "load_questions",
    "load_results_csv",
    "load_traces_jsonl",
    "run_load_scenario",
    "run_retrieval_eval",
]
