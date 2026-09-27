from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from typer.testing import CliRunner

from tests.mock_cloud import MockCloud
from tests.mock_mlx_serve import MockMlxServe
from video_security import db, evalrun
from video_security.cli import app
from video_security.config import Config
from video_security.db import connect, init_db
from video_security.evalrun import (
    CaseResult,
    EvalCase,
    EvalScores,
    PassOutcome,
    StratumScore,
)
from video_security.llm.cloud import (
    CloudClient,
    CloudDisabledError,
    CloudError,
    make_cloud_client,
)

runner = CliRunner()

FIXTURE_CASES = Path(__file__).parent / "fixtures" / "eval_cases.toml"

VERDICT_TRUE = (
    '{"relevant": true, "event_type": "intrusion", '
    '"description": "person", "confidence": "high"}'
)
VERDICT_FALSE = (
    '{"relevant": false, "event_type": "none", '
    '"description": "nothing", "confidence": "low"}'
)

CLOUD_PRICES = (
    "price_per_1m_input_tokens = 1000.0\n"
    "price_per_1m_output_tokens = 2000.0\n"
    "price_per_image = 0.5\n"
)


def _seed_db(db_path: Path, frames_dir: Path) -> None:
    conn = connect(str(db_path))
    init_db(conn)
    db.create_job(conn, "/videos/clip1.mp4", "hash-1")
    frames_dir.mkdir(parents=True, exist_ok=True)
    kf_paths = []
    for i in range(3):
        p = frames_dir / f"kf_{i}.jpg"
        cv2.imwrite(str(p), np.full((48, 64, 3), 40 * (i + 1), dtype=np.uint8))
        kf_paths.append(str(p))
    ids = []
    for i in range(4):
        eid = db.insert_event(
            conn,
            1,
            "intrusion",
            float(i),
            float(i) + 1.0,
            1,
            None,
            json.dumps(kf_paths),
            0.8,
            0.9,
        )
        ids.append(eid)
    assert ids == [1, 2, 3, 4]
    conn.close()


def _write_config(
    path: Path, db_path: Path, artifact_dir: Path, body: str
) -> Path:
    path.write_text(
        f'[storage]\ndb_path = "{db_path}"\n'
        f'artifact_dir = "{artifact_dir}"\n' + body
    )
    return path


def _mk_result(
    case: EvalCase,
    got: bool | None,
    event_type: str = "intrusion",
    confidence: str = "high",
) -> CaseResult:
    triage = PassOutcome(
        stage="triage",
        ok=got is not None,
        relevant=bool(got),
        event_type=event_type,
        confidence=confidence,
        error="" if got is not None else "boom",
    )
    return CaseResult(
        case=case,
        triage=triage,
        detail=None,
        got_relevant=got,
        got_event_type=event_type if got is not None else "",
        got_confidence=confidence if got is not None else "",
        got_stage="triage",
        error=triage.error,
    )


def test_load_cases_fixture_format() -> None:
    cases = evalrun.load_cases(FIXTURE_CASES)
    assert len(cases) == 4
    c1 = cases[0]
    assert (c1.job_id, c1.event_id) == (1, 1)
    assert c1.expected_relevant is True
    assert c1.expected_event_type == "intrusion"
    assert c1.expected_confidence == "high"
    assert c1.stratum == "night"
    assert "door" in c1.note
    assert c1.ref == "job 1 event 1"
    assert c1.label == "job 1 event 1 [night]"
    c2 = cases[1]
    assert c2.expected_relevant is True
    assert c2.expected_event_type is None
    assert c2.expected_confidence is None
    c3 = cases[2]
    assert c3.expected_relevant is False
    assert c3.stratum == "night"


def test_load_cases_missing(tmp_path: Path) -> None:
    with pytest.raises(evalrun.CasesError, match="--cases"):
        evalrun.load_cases(tmp_path / "nope.toml")


def test_load_cases_bad_shape(tmp_path: Path) -> None:
    p = tmp_path / "cases.toml"
    p.write_text('[[case]]\njob_id = 1\nevent_id = 2\nstratum = "x"\n')
    with pytest.raises(evalrun.CasesError, match="expected\\.relevant"):
        evalrun.load_cases(p)


def test_scorer_confusion_and_strata() -> None:
    combos = [(True, True), (True, False), (False, True), (False, False)]
    results = []
    for i, (exp, got) in enumerate(combos, 1):
        case = EvalCase(
            job_id=1,
            event_id=i,
            expected_relevant=exp,
            expected_event_type="intrusion" if exp else None,
            stratum="s1" if i % 2 else "s2",
        )
        results.append(_mk_result(case, got))
    scores = evalrun.score_results(results)
    assert (scores.overall.tp, scores.overall.fp) == (1, 1)
    assert (scores.overall.fn, scores.overall.tn) == (1, 1)
    assert scores.overall.precision == pytest.approx(0.5)
    assert scores.overall.recall == pytest.approx(0.5)
    assert scores.overall.f1 == pytest.approx(0.5)
    assert (scores.per_stratum["s1"].tp, scores.per_stratum["s1"].fp) == (1, 1)
    assert (scores.per_stratum["s2"].fn, scores.per_stratum["s2"].tn) == (1, 1)
    assert scores.event_type_compared == 1
    assert scores.event_type_matched == 1
    assert scores.confidence_counts == {"high": 2}
    empty = StratumScore()
    assert empty.precision == 0.0
    assert empty.recall == 0.0
    assert empty.f1 == 0.0


def test_scorer_confidence_bands() -> None:
    case_a = EvalCase(
        job_id=1,
        event_id=1,
        expected_relevant=True,
        expected_confidence="high",
    )
    case_b = EvalCase(
        job_id=1,
        event_id=2,
        expected_relevant=True,
        expected_confidence="high",
    )
    results = [
        _mk_result(case_a, True, confidence="high"),
        _mk_result(case_b, True, confidence="medium"),
    ]
    scores = evalrun.score_results(results)
    assert scores.confidence_counts == {"high": 1, "medium": 1}
    assert scores.confidence_expected == 2
    assert scores.confidence_matched == 1


def test_gate_regressed_pure() -> None:
    baseline = {"scores": {"precision": 0.8, "recall": 1.0}}
    regressed = EvalScores(overall=StratumScore(tp=1, fp=1, fn=1, tn=1))
    assert evalrun.gate_regressed(baseline, regressed) is True
    unchanged = EvalScores(overall=StratumScore(tp=8, fp=2, fn=0, tn=0))
    assert evalrun.gate_regressed(baseline, unchanged) is False


def test_cloud_client_auth_and_image_parts() -> None:
    with MockCloud(auth_token="test-key") as m:
        client = CloudClient(
            m.base_url, "mock-cloud-vlm", "wrong-key", timeout_s=5, max_attempts=1
        )
        with pytest.raises(CloudError, match="HTTP 401"):
            client.generate_json("hello", images=["aGVsbG8="])
        assert len(m.requests) == 1
        body = m.requests[0]["body"]
        content = body["messages"][0]["content"]
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "image_url"
        assert content[1]["image_url"]["url"].startswith(
            "data:image/jpeg;base64,"
        )


def test_cloud_client_usage_roundtrip() -> None:
    with MockCloud(auth_token="test-key") as m:
        client = CloudClient(
            m.base_url, "mock-cloud-vlm", "test-key", timeout_s=5
        )
        result = client.generate_json("hello")
        assert result.data == {
            "relevant": True,
            "event_type": "intrusion",
            "description": "ok",
            "confidence": "high",
        }
        assert result.prompt_tokens == 120
        assert result.completion_tokens == 40
        assert result.model == "mock-cloud-vlm"


def test_cloud_disabled_error_messages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VS_CLOUD_API_KEY", raising=False)
    cfg = Config()
    with pytest.raises(CloudDisabledError, match="enabled"):
        make_cloud_client(cfg)
    cfg.cloud.enabled = True
    with pytest.raises(CloudDisabledError, match="base_url"):
        make_cloud_client(cfg)
    cfg.cloud.base_url = "http://127.0.0.1:1"
    with pytest.raises(CloudDisabledError, match="model"):
        make_cloud_client(cfg)
    cfg.cloud.model = "m"
    with pytest.raises(CloudDisabledError, match="VS_CLOUD_API_KEY"):
        make_cloud_client(cfg)


def test_eval_local_end_to_end(tmp_path: Path) -> None:
    db_path = tmp_path / "db"
    _seed_db(db_path, tmp_path / "frames")
    mock = MockMlxServe(
        response_text=VERDICT_TRUE,
        models=["gemma3:4b", "gemma4:12b"],
    )
    with mock.start() as m:
        cfg = _write_config(
            tmp_path / "cfg.toml",
            db_path,
            tmp_path / "artifacts",
            f'[llm]\nprovider = "mlx-serve"\nmlx_url = "{m.base_url}"\n',
        )
        result = runner.invoke(
            app, ["--config", str(cfg), "eval", "--cases", str(FIXTURE_CASES)]
        )
        assert result.exit_code == 0, result.output
        assert "precision: 0.5000  recall: 1.0000  f1: 0.6667" in result.output
        assert "confusion: TP 2  FP 2  FN 0  TN 0" in result.output
        assert "per-stratum:" in result.output
        assert "disagreements:" in result.output
        assert "job 1 event 3 [night]" in result.output
        chats = [r for r in m.requests if r["path"] == "/v1/chat/completions"]
        assert len(chats) == 6
        triage_content = chats[0]["body"]["messages"][0]["content"]
        assert len(triage_content) == 2
        detail_content = chats[1]["body"]["messages"][0]["content"]
        assert len(detail_content) == 4
    conn = connect(str(db_path))
    assert conn.execute("SELECT COUNT(*) FROM analysis_results").fetchone()[0] == 0
    statuses = {r["status"] for r in conn.execute("SELECT status FROM events")}
    assert statuses == {"pending"}
    conn.close()


def test_eval_cloud_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "db"
    _seed_db(db_path, tmp_path / "frames")
    monkeypatch.setenv("VS_CLOUD_API_KEY", "test-key")
    responses = [VERDICT_TRUE, VERDICT_TRUE, VERDICT_FALSE, VERDICT_FALSE]
    with MockCloud(responses=responses) as m:
        cfg = _write_config(
            tmp_path / "cfg.toml",
            db_path,
            tmp_path / "artifacts",
            (
                f'[cloud]\nenabled = true\nbase_url = "{m.base_url}"\n'
                'model = "mock-cloud-vlm"\n'
                "monthly_budget_usd = 10.0\n" + CLOUD_PRICES
            ),
        )
        result = runner.invoke(
            app,
            [
                "--config",
                str(cfg),
                "eval",
                "--cases",
                str(FIXTURE_CASES),
                "--backend",
                "cloud",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "precision: 0.3333  recall: 0.5000" in result.output
        assert "cloud usage: 6 calls" in result.output
        assert "ledger this month: $6.2000" in result.output
        assert len(m.requests) == 6
        first = m.requests[0]
        assert first["headers"].get("Authorization") == "Bearer test-key"
        assert first["body"]["model"] == "mock-cloud-vlm"
        assert first["body"]["temperature"] == 0
        assert first["body"]["max_tokens"] == 2048
    conn = connect(str(db_path))
    rows = conn.execute("SELECT * FROM cloud_calls ORDER BY id").fetchall()
    assert len(rows) == 6
    assert all(r["purpose"] == "eval" for r in rows)
    assert all(r["model"] == "mock-cloud-vlm" for r in rows)
    assert all(r["base_url"] for r in rows)
    assert {r["ref"] for r in rows} == {f"job 1 event {i}" for i in (1, 2, 3, 4)}
    assert all(r["input_tokens"] == 120 for r in rows)
    assert all(r["output_tokens"] == 40 for r in rows)
    assert all(r["est_cost_usd"] > 0 for r in rows)
    assert sorted(r["images"] for r in rows) == [1, 1, 1, 1, 3, 3]
    assert db.cloud_spend_month(conn) == pytest.approx(6.2)
    conn.close()


def test_eval_cloud_budget_exhaustion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "db"
    _seed_db(db_path, tmp_path / "frames")
    monkeypatch.setenv("VS_CLOUD_API_KEY", "test-key")
    with MockCloud() as m:
        cfg = _write_config(
            tmp_path / "cfg.toml",
            db_path,
            tmp_path / "artifacts",
            (
                f'[cloud]\nenabled = true\nbase_url = "{m.base_url}"\n'
                'model = "mock-cloud-vlm"\n'
                "monthly_budget_usd = 0.001\n" + CLOUD_PRICES
            ),
        )
        result = runner.invoke(
            app,
            [
                "--config",
                str(cfg),
                "eval",
                "--cases",
                str(FIXTURE_CASES),
                "--backend",
                "cloud",
            ],
        )
        assert result.exit_code == 1, result.output
        assert "cloud budget cap" in result.output
        assert "monthly_budget_usd" in result.output
        assert "spent this month" in result.output
        assert m.requests == []
    conn = connect(str(db_path))
    assert conn.execute("SELECT COUNT(*) FROM cloud_calls").fetchone()[0] == 0
    conn.close()


def test_eval_cloud_refusals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "db"
    _seed_db(db_path, tmp_path / "frames")
    cloud_body = (
        '[cloud]\nenabled = true\nbase_url = "http://127.0.0.1:1"\n'
        'model = "mock-cloud-vlm"\n'
        "monthly_budget_usd = 10.0\n"
    )
    cases = [
        (
            "disabled",
            '[cloud]\nenabled = false\nbase_url = "http://127.0.0.1:1"\n'
            'model = "mock-cloud-vlm"\n'
            "monthly_budget_usd = 10.0\n",
            "enabled = true",
        ),
        ("missing key", cloud_body, "VS_CLOUD_API_KEY"),
        (
            "zero budget",
            '[cloud]\nenabled = true\nbase_url = "http://127.0.0.1:1"\n'
            'model = "mock-cloud-vlm"\n'
            "monthly_budget_usd = 0.0\n",
            "monthly_budget_usd > 0",
        ),
    ]
    with MockCloud() as m:
        for name, body, needle in cases:
            if name == "missing key":
                monkeypatch.delenv("VS_CLOUD_API_KEY", raising=False)
            else:
                monkeypatch.setenv("VS_CLOUD_API_KEY", "test-key")
            cfg = _write_config(
                tmp_path / f"cfg_{name}.toml",
                db_path,
                tmp_path / "artifacts",
                body,
            )
            result = runner.invoke(
                app,
                [
                    "--config",
                    str(cfg),
                    "eval",
                    "--cases",
                    str(FIXTURE_CASES),
                    "--backend",
                    "cloud",
                ],
            )
            assert result.exit_code == 1, (name, result.output)
            assert needle in result.output, (name, result.output)
        assert m.requests == []
    conn = connect(str(db_path))
    assert conn.execute("SELECT COUNT(*) FROM cloud_calls").fetchone()[0] == 0
    conn.close()


def test_eval_cloud_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "db"
    _seed_db(db_path, tmp_path / "frames")
    monkeypatch.setenv("VS_CLOUD_API_KEY", "test-key")
    with MockCloud() as m:
        cfg = _write_config(
            tmp_path / "cfg.toml",
            db_path,
            tmp_path / "artifacts",
            (
                f'[cloud]\nenabled = true\nbase_url = "{m.base_url}"\n'
                'model = "mock-cloud-vlm"\n'
                "monthly_budget_usd = 10.0\n" + CLOUD_PRICES
            ),
        )
        result = runner.invoke(
            app,
            [
                "--config",
                str(cfg),
                "eval",
                "--cases",
                str(FIXTURE_CASES),
                "--backend",
                "cloud",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "— eval estimate —" in result.output
        assert "cases: 4 (2 positive, 2 negative)" in result.output
        assert "calls: 4 triage + 2 detail = 6" in result.output
        assert "images: 10" in result.output
        assert "est tokens: in ~" in result.output
        assert "est cost: $" in result.output
        assert m.requests == []
    conn = connect(str(db_path))
    assert conn.execute("SELECT COUNT(*) FROM cloud_calls").fetchone()[0] == 0
    conn.close()


def test_eval_local_dry_run(tmp_path: Path) -> None:
    db_path = tmp_path / "db"
    _seed_db(db_path, tmp_path / "frames")
    with MockMlxServe(models=["gemma3:4b", "gemma4:12b"]) as m:
        cfg = _write_config(
            tmp_path / "cfg.toml",
            db_path,
            tmp_path / "artifacts",
            f'[llm]\nprovider = "mlx-serve"\nmlx_url = "{m.base_url}"\n',
        )
        result = runner.invoke(
            app,
            [
                "--config",
                str(cfg),
                "eval",
                "--cases",
                str(FIXTURE_CASES),
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "cost: local — $0" in result.output
        assert m.requests == []


def test_baseline_save_compare_gate(tmp_path: Path) -> None:
    db_path = tmp_path / "db"
    _seed_db(db_path, tmp_path / "frames")
    baseline_path = tmp_path / "baseline.json"
    with MockMlxServe(
        response_text=VERDICT_TRUE, models=["gemma3:4b", "gemma4:12b"]
    ) as m:
        cfg = _write_config(
            tmp_path / "cfg.toml",
            db_path,
            tmp_path / "artifacts",
            f'[llm]\nprovider = "mlx-serve"\nmlx_url = "{m.base_url}"\n',
        )
        base_args = ["--config", str(cfg), "eval", "--cases", str(FIXTURE_CASES)]
        result = runner.invoke(app, base_args + ["--save-baseline", str(baseline_path)])
        assert result.exit_code == 0, result.output
        assert "baseline saved" in result.output
        data = json.loads(baseline_path.read_text())
        assert data["case_count"] == 4
        assert data["backend"] == "local"
        assert data["models"]["triage"] == "gemma3:4b"
        assert data["models"]["detail"] == "gemma4:12b"
        assert data["scores"]["precision"] == pytest.approx(0.5)
        assert data["scores"]["recall"] == pytest.approx(1.0)
        assert "night" in data["per_stratum"]

        result = runner.invoke(
            app, base_args + ["--compare-baseline", str(baseline_path)]
        )
        assert result.exit_code == 0, result.output
        assert "precision: 0.5000 -> 0.5000 (unchanged)" in result.output
        assert "gate" not in result.output

        inflated = dict(data)
        inflated["scores"] = {"precision": 1.0, "recall": 1.0, "f1": 1.0}
        inflated_path = tmp_path / "inflated.json"
        inflated_path.write_text(json.dumps(inflated))
        result = runner.invoke(
            app,
            base_args
            + ["--compare-baseline", str(inflated_path), "--gate"],
        )
        assert result.exit_code == 1, result.output
        assert "regressed" in result.output

        deflated = dict(data)
        deflated["scores"] = {"precision": 0.0, "recall": 0.0, "f1": 0.0}
        deflated_path = tmp_path / "deflated.json"
        deflated_path.write_text(json.dumps(deflated))
        result = runner.invoke(
            app,
            base_args
            + ["--compare-baseline", str(deflated_path), "--gate"],
        )
        assert result.exit_code == 0, result.output
        assert "no precision/recall regression" in result.output


def test_eval_gate_without_compare_baseline(tmp_path: Path) -> None:
    db_path = tmp_path / "db"
    _seed_db(db_path, tmp_path / "frames")
    with MockMlxServe(models=["gemma3:4b", "gemma4:12b"]) as m:
        cfg = _write_config(
            tmp_path / "cfg.toml",
            db_path,
            tmp_path / "artifacts",
            f'[llm]\nprovider = "mlx-serve"\nmlx_url = "{m.base_url}"\n',
        )
        result = runner.invoke(
            app,
            [
                "--config",
                str(cfg),
                "eval",
                "--cases",
                str(FIXTURE_CASES),
                "--gate",
            ],
        )
        assert result.exit_code == 1, result.output
        assert "--gate requires --compare-baseline" in result.output


def test_eval_model_override_and_limit(tmp_path: Path) -> None:
    db_path = tmp_path / "db"
    _seed_db(db_path, tmp_path / "frames")
    with MockMlxServe(models=["alt-model"]) as m:
        cfg = _write_config(
            tmp_path / "cfg.toml",
            db_path,
            tmp_path / "artifacts",
            f'[llm]\nprovider = "mlx-serve"\nmlx_url = "{m.base_url}"\n',
        )
        result = runner.invoke(
            app,
            [
                "--config",
                str(cfg),
                "eval",
                "--cases",
                str(FIXTURE_CASES),
                "--model",
                "alt-model",
                "--limit",
                "2",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "cases: 2 (2 positive, 0 negative)" in result.output
        assert "models: triage=alt-model detail=alt-model" in result.output
        chats = [r for r in m.requests if r["path"] == "/v1/chat/completions"]
        assert len(chats) == 4
        assert all(c["body"]["model"] == "alt-model" for c in chats)


def test_eval_missing_event_recorded(tmp_path: Path) -> None:
    db_path = tmp_path / "db"
    _seed_db(db_path, tmp_path / "frames")
    with MockMlxServe(models=["gemma3:4b", "gemma4:12b"]) as m:
        cfg = Config()
        cfg.llm.provider = "mlx-serve"
        cfg.llm.mlx_url = m.base_url
        conn = connect(str(db_path))
        init_db(conn)
        cases = [
            EvalCase(job_id=99, event_id=7, expected_relevant=True),
            EvalCase(job_id=1, event_id=1, expected_relevant=False),
        ]
        report = evalrun.run_eval(conn, cfg, cases, backend="local")
        assert report.results[0].got_relevant is None
        assert "event 7 not found in job 99" in report.results[0].error
        assert report.results[1].got_relevant is True
        assert "errors: 1" in evalrun.format_report(report)
        conn.close()
