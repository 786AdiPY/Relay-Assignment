"""Unit tests verifying Layer 5 exact 12-column results.csv and table recomputation."""

import tempfile
from pathlib import Path

from relay.eval.tables import (
    compute_retrieval_table,
    compute_scenario_tables,
    load_results_csv,
)
from relay.observability.bus import TelemetryBus
from relay.observability.models import RESULTS_CSV_COLUMNS


def test_results_csv_schema_and_flushing():
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        bus = TelemetryBus(output_dir=out_dir)

        tr = bus.record_arrival(
            request_id="req_001",
            scenario="steady",
            source="stand-in",
            deadline_ms=5000.0,
            retrieval_setting="med_k5_rerank",
            question_id="q01",
        )
        bus.record_queue_enter("req_001", 2)
        bus.record_queue_exit("req_001", 1)
        bus.record_first_verified_token("req_001")
        bus.record_completion(
            "req_001",
            status="ok",
            answer_text="Test answer",
            citations=["[Attention, 1706.03762v7, p.3]"],
            answer_grade="correct",
        )

        csv_path, jsonl_path = bus.flush()
        assert csv_path.exists()
        assert jsonl_path.exists()

        rows = load_results_csv(csv_path)
        assert len(rows) == 1
        row = rows[0]
        assert tuple(row.keys()) == RESULTS_CSV_COLUMNS
        assert row["request_id"] == "req_001"
        assert row["scenario"] == "steady"
        assert row["source"] == "stand-in"
        assert row["status"] == "ok"
        assert row["answer_grade"] == "correct"
        assert float(row["first_token_ms"]) >= 0.0
        assert float(row["total_ms"]) >= float(row["first_token_ms"])


def test_table_recomputation_math():
    sample_rows = [
        {
            "request_id": "req_1",
            "scenario": "steady",
            "source": "stand-in",
            "start_time": "2026-11-12T10:00:00.000Z",
            "first_token_ms": "120.0",
            "total_ms": "300.0",
            "status": "ok",
            "retries": "0",
            "tokens_in": "400",
            "tokens_out": "100",
            "retrieval_setting": "med_k5_rerank",
            "answer_grade": "correct",
        },
        {
            "request_id": "req_2",
            "scenario": "steady",
            "source": "stand-in",
            "start_time": "2026-11-12T10:00:01.000Z",
            "first_token_ms": "",
            "total_ms": "50.0",
            "status": "rate_limited",
            "retries": "1",
            "tokens_in": "0",
            "tokens_out": "0",
            "retrieval_setting": "med_k5_rerank",
            "answer_grade": "",
        },
    ]

    t2a, t2b = compute_scenario_tables(sample_rows, max_concurrency=2)
    assert len(t2a) == 1
    assert len(t2b) == 1
    assert t2a[0]["Scenario"] == "steady"
    assert t2a[0]["Requests sent"] == 2
    assert t2b[0]["Succeeded %"] == 50.0
    assert t2b[0]["Rate-limited %"] == 50.0
    assert t2b[0]["Retries per request"] == 0.5

    retrieval_rows = [
        {
            "request_id": "ret_1",
            "scenario": "retrieval_med_k5_rerank",
            "source": "stand-in",
            "start_time": "2026-11-12T10:00:00.000Z",
            "first_token_ms": "120.0",
            "total_ms": "300.0",
            "status": "ok",
            "retries": "0",
            "tokens_in": "400",
            "tokens_out": "100",
            "retrieval_setting": "med_k5_rerank",
            "answer_grade": "correct",
        },
        {
            "request_id": "ret_2",
            "scenario": "retrieval_med_k5_rerank",
            "source": "stand-in",
            "start_time": "2026-11-12T10:00:01.000Z",
            "first_token_ms": "130.0",
            "total_ms": "250.0",
            "status": "ok",
            "retries": "0",
            "tokens_in": "350",
            "tokens_out": "90",
            "retrieval_setting": "med_k5_rerank",
            "answer_grade": "wrong",
        },
    ]

    t3 = compute_retrieval_table(retrieval_rows)
    assert len(t3) == 1
    assert t3[0]["Questions asked"] == 2
    assert t3[0]["Correct and cited %"] == 50.0
    assert t3[0]["Wrong %"] == 50.0
