from __future__ import annotations

import dataclasses
import json
import sqlite3
import tomllib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from video_security import db
from video_security.config import CloudConfig, Config
from video_security.llm import LLMClient, make_llm_client
from video_security.llm.cloud import (
    BudgetExceededError,
    CloudClient,
    CloudError,
)
from video_security.llm.ollama import OllamaError, encode_image_jpeg
from video_security.llm.prompts import (
    DETAIL_SCHEMA,
    TRIAGE_SCHEMA,
    build_detail_prompt,
    build_triage_prompt,
)
from video_security.llm.triage import LLMEvent
from video_security.pipeline import load_llm_events

DEFAULT_CASES_PATH = "~/.video-security/eval/cases.toml"
OUTPUT_TOKEN_ESTIMATE = 256
IMAGE_TOKEN_ESTIMATE = 1000
DETAIL_KEYFRAMES = 3
BACKENDS = ("local", "cloud")


class CasesError(Exception):
    pass


@dataclasses.dataclass
class EvalCase:
    job_id: int
    event_id: int
    expected_relevant: bool
    expected_event_type: str | None = None
    expected_confidence: str | None = None
    stratum: str = ""
    note: str = ""

    @property
    def ref(self) -> str:
        return f"job {self.job_id} event {self.event_id}"

    @property
    def label(self) -> str:
        out = self.ref
        if self.stratum:
            out += f" [{self.stratum}]"
        return out


@dataclasses.dataclass
class PassOutcome:
    stage: str
    ok: bool = False
    relevant: bool = False
    event_type: str = ""
    confidence: str = ""
    error: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    images: int = 0
    est_cost_usd: float = 0.0


@dataclasses.dataclass
class CaseResult:
    case: EvalCase
    triage: PassOutcome
    detail: PassOutcome | None
    got_relevant: bool | None
    got_event_type: str
    got_confidence: str
    got_stage: str
    error: str


@dataclasses.dataclass
class StratumScore:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0

    @property
    def precision(self) -> float:
        denom = self.tp + self.fp
        return self.tp / denom if denom else 0.0

    @property
    def recall(self) -> float:
        denom = self.tp + self.fn
        return self.tp / denom if denom else 0.0

    @property
    def f1(self) -> float:
        denom = self.precision + self.recall
        return 2 * self.precision * self.recall / denom if denom else 0.0

    def to_dict(self) -> dict[str, float]:
        return {
            "tp": float(self.tp),
            "fp": float(self.fp),
            "fn": float(self.fn),
            "tn": float(self.tn),
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
        }


@dataclasses.dataclass
class EvalScores:
    overall: StratumScore = dataclasses.field(default_factory=StratumScore)
    per_stratum: dict[str, StratumScore] = dataclasses.field(default_factory=dict)
    event_type_compared: int = 0
    event_type_matched: int = 0
    confidence_counts: dict[str, int] = dataclasses.field(default_factory=dict)
    confidence_expected: int = 0
    confidence_matched: int = 0


@dataclasses.dataclass
class CloudUsage:
    calls: int = 0
    images: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    est_cost_usd: float = 0.0

    def add(self, outcome: PassOutcome) -> None:
        if not outcome.ok:
            return
        self.calls += 1
        self.images += outcome.images
        self.input_tokens += outcome.input_tokens
        self.output_tokens += outcome.output_tokens
        self.est_cost_usd += outcome.est_cost_usd


@dataclasses.dataclass
class EvalReport:
    backend: str
    models: dict[str, str]
    results: list[CaseResult]
    scores: EvalScores
    cloud_usage: CloudUsage
    month_spend: float


def load_cases(path: Path) -> list[EvalCase]:
    if not path.is_file():
        raise CasesError(
            f"cases file not found: {path} — create one or pass --cases PATH "
            f"(format example: tests/fixtures/eval_cases.toml)"
        )
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise CasesError(f"TOML parse error in {path}: {e}") from e
    entries = data.get("case", [])
    if not isinstance(entries, list) or not entries:
        raise CasesError(f"no [[case]] entries in {path}")
    return [_parse_case(entry, i + 1, path) for i, entry in enumerate(entries)]


def _parse_case(entry: Any, n: int, path: Path) -> EvalCase:
    where = f"case #{n} in {path}"
    if not isinstance(entry, dict):
        raise CasesError(f"{where}: must be a table")
    job_id = entry.get("job_id")
    event_id = entry.get("event_id")
    if not isinstance(job_id, int) or isinstance(job_id, bool):
        raise CasesError(f"{where}: job_id must be an int")
    if not isinstance(event_id, int) or isinstance(event_id, bool):
        raise CasesError(f"{where}: event_id must be an int")
    expected = entry.get("expected")
    if not isinstance(expected, dict) or not isinstance(expected.get("relevant"), bool):
        raise CasesError(f"{where}: expected.relevant (bool) is required")
    event_type = expected.get("event_type")
    if event_type is not None and not isinstance(event_type, str):
        raise CasesError(f"{where}: expected.event_type must be a string")
    confidence = expected.get("confidence")
    if confidence is not None and not isinstance(confidence, str):
        raise CasesError(f"{where}: expected.confidence must be a string")
    stratum = entry.get("stratum", "")
    if not isinstance(stratum, str):
        raise CasesError(f"{where}: stratum must be a string")
    note = entry.get("note", "")
    if not isinstance(note, str):
        raise CasesError(f"{where}: note must be a string")
    return EvalCase(
        job_id=job_id,
        event_id=event_id,
        expected_relevant=expected["relevant"],
        expected_event_type=event_type,
        expected_confidence=confidence,
        stratum=stratum,
        note=note,
    )


def estimate_tokens(text_chars: int, images: int) -> tuple[int, int]:
    return text_chars // 4 + images * IMAGE_TOKEN_ESTIMATE, OUTPUT_TOKEN_ESTIMATE


def call_cost_usd(
    cloud: CloudConfig, input_tokens: int, output_tokens: int, images: int
) -> float:
    return (
        input_tokens / 1e6 * cloud.price_per_1m_input_tokens
        + output_tokens / 1e6 * cloud.price_per_1m_output_tokens
        + images * cloud.price_per_image
    )


def check_cloud_budget(
    conn: sqlite3.Connection, cloud: CloudConfig, est_cost: float
) -> None:
    if cloud.monthly_budget_usd <= 0:
        raise BudgetExceededError(
            "[cloud] monthly_budget_usd is 0 — set a monthly cap before any cloud calls"
        )
    spent = db.cloud_spend_month(conn)
    if spent + est_cost > cloud.monthly_budget_usd:
        raise BudgetExceededError(
            f"cloud budget cap: ${spent:.4f} spent this month + "
            f"${est_cost:.4f} estimated for this call exceeds the "
            f"${cloud.monthly_budget_usd:.2f} monthly_budget_usd cap — "
            "raise the cap or run with --dry-run"
        )


def _events_by_job(
    conn: sqlite3.Connection, job_id: int
) -> dict[int, LLMEvent]:
    return {e.id: e for e in load_llm_events(conn, job_id)}


def _cloud_pass(
    conn: sqlite3.Connection,
    cloud: CloudConfig,
    client: CloudClient,
    stage: str,
    prompt: str,
    images: list[str] | None,
    schema: dict[str, Any],
    case: EvalCase,
) -> PassOutcome:
    n_images = len(images) if images else 0
    est_in, est_out = estimate_tokens(len(prompt), n_images)
    check_cloud_budget(conn, cloud, call_cost_usd(cloud, est_in, est_out, n_images))
    try:
        result = client.generate_json(prompt, images, schema)
    except CloudError as e:
        return PassOutcome(stage=stage, ok=False, error=str(e), images=n_images)
    cost = call_cost_usd(
        cloud, result.prompt_tokens, result.completion_tokens, n_images
    )
    db.insert_cloud_call(
        conn,
        purpose="eval",
        base_url=client.base_url,
        model=result.model,
        input_tokens=result.prompt_tokens,
        output_tokens=result.completion_tokens,
        images=n_images,
        est_cost_usd=cost,
        ref=case.ref,
    )
    return PassOutcome(
        stage=stage,
        ok=True,
        relevant=bool(result.data.get("relevant", False)),
        event_type=str(result.data.get("event_type", "none")),
        confidence=str(result.data.get("confidence", "low")),
        input_tokens=result.prompt_tokens,
        output_tokens=result.completion_tokens,
        images=n_images,
        est_cost_usd=cost,
    )


def _local_pass(
    stage: str,
    client: LLMClient,
    model: str,
    num_ctx: int,
    prompt: str,
    images: list[str] | None,
    schema: dict[str, Any],
) -> PassOutcome:
    n_images = len(images) if images else 0
    try:
        data = client.generate_json(model, prompt, images, schema, num_ctx=num_ctx)
    except OllamaError as e:
        return PassOutcome(stage=stage, ok=False, error=str(e), images=n_images)
    return PassOutcome(
        stage=stage,
        ok=True,
        relevant=bool(data.get("relevant", False)),
        event_type=str(data.get("event_type", "none")),
        confidence=str(data.get("confidence", "low")),
    )


def _run_pass(
    conn: sqlite3.Connection,
    cfg: Config,
    case: EvalCase,
    event: LLMEvent,
    stage: str,
    backend: str,
    local_client: LLMClient | None,
    cloud_client: CloudClient | None,
) -> PassOutcome:
    if stage == "triage":
        keyframe = event.keyframes[0] if event.keyframes else None
        images = [encode_image_jpeg(keyframe)] if keyframe is not None else None
        prompt = build_triage_prompt(
            event.event_type,
            event.detector_score,
            event.start_sec,
            event.transcript_window,
            evidence_summary=event.evidence_summary,
        )
        model = cfg.llm_triage.model
        num_ctx = cfg.llm_triage.num_ctx
        schema: dict[str, Any] = TRIAGE_SCHEMA
    else:
        keyframes = event.keyframes[:DETAIL_KEYFRAMES]
        images = [encode_image_jpeg(k) for k in keyframes] if keyframes else None
        prompt = build_detail_prompt(
            event.event_type,
            event.detector_score,
            event.start_sec,
            event.end_sec,
            event.transcript_window,
            tiled=False,
            evidence_summary=event.evidence_summary,
        )
        model = cfg.llm_detail.model
        num_ctx = cfg.llm_detail.num_ctx
        schema = DETAIL_SCHEMA
    if backend == "cloud":
        assert cloud_client is not None
        return _cloud_pass(
            conn, cfg.cloud, cloud_client, stage, prompt, images, schema, case
        )
    assert local_client is not None
    return _local_pass(stage, local_client, model, num_ctx, prompt, images, schema)


def run_eval(
    conn: sqlite3.Connection,
    cfg: Config,
    cases: list[EvalCase],
    backend: str = "local",
    model_override: str | None = None,
    on_result: Callable[[CaseResult], None] | None = None,
) -> EvalReport:
    if backend not in BACKENDS:
        raise CasesError(
            f"unknown backend {backend!r} — choose one of: {', '.join(BACKENDS)}"
        )
    if backend == "cloud":
        cloud_cfg = cfg.cloud
        if model_override:
            cloud_cfg = dataclasses.replace(cloud_cfg, model=model_override)
        cfg = dataclasses.replace(cfg, cloud=cloud_cfg)
        cloud_client: CloudClient | None = make_llm_client(cfg, backend="cloud")
        local_client: LLMClient | None = None
        assert cloud_client is not None
        models = {"triage": cloud_client.model, "detail": cloud_client.model}
    else:
        if model_override:
            cfg = dataclasses.replace(
                cfg,
                llm_triage=dataclasses.replace(cfg.llm_triage, model=model_override),
                llm_detail=dataclasses.replace(cfg.llm_detail, model=model_override),
            )
        cloud_client = None
        local_client = make_llm_client(cfg)
        models = {
            "triage": local_client.model_digest(cfg.llm_triage.model),
            "detail": local_client.model_digest(cfg.llm_detail.model),
        }

    cache: dict[int, dict[int, LLMEvent]] = {}
    results: list[CaseResult] = []
    usage = CloudUsage()
    for case in cases:
        if case.job_id not in cache:
            cache[case.job_id] = _events_by_job(conn, case.job_id)
        event = cache[case.job_id].get(case.event_id)
        if event is None:
            results.append(
                CaseResult(
                    case=case,
                    triage=PassOutcome(stage="triage"),
                    detail=None,
                    got_relevant=None,
                    got_event_type="",
                    got_confidence="",
                    got_stage="",
                    error=f"event {case.event_id} not found in job {case.job_id}",
                )
            )
            if on_result is not None:
                on_result(results[-1])
            continue
        triage_out = _run_pass(
            conn, cfg, case, event, "triage", backend, local_client, cloud_client
        )
        if backend == "cloud":
            usage.add(triage_out)
        detail_out: PassOutcome | None = None
        if case.expected_relevant:
            detail_out = _run_pass(
                conn, cfg, case, event, "detail", backend, local_client, cloud_client
            )
            if backend == "cloud":
                usage.add(detail_out)
        source: PassOutcome | None = None
        if detail_out is not None and detail_out.ok:
            source = detail_out
        elif triage_out.ok:
            source = triage_out
        errors = [o.error for o in (triage_out, detail_out) if o is not None and o.error]
        if source is not None:
            got_stage = source.stage
            got_relevant: bool | None = source.relevant
            got_type = source.event_type
            got_conf = source.confidence
        else:
            got_stage = ""
            got_relevant = None
            got_type = ""
            got_conf = ""
        results.append(
            CaseResult(
                case=case,
                triage=triage_out,
                detail=detail_out,
                got_relevant=got_relevant,
                got_event_type=got_type,
                got_confidence=got_conf,
                got_stage=got_stage,
                error="; ".join(errors),
            )
        )
        if on_result is not None:
            on_result(results[-1])

    return EvalReport(
        backend=backend,
        models=models,
        results=results,
        scores=score_results(results),
        cloud_usage=usage,
        month_spend=db.cloud_spend_month(conn),
    )


def _update_confusion(score: StratumScore, expected: bool, got: bool) -> None:
    if expected and got:
        score.tp += 1
    elif not expected and got:
        score.fp += 1
    elif expected and not got:
        score.fn += 1
    else:
        score.tn += 1


def score_results(results: list[CaseResult]) -> EvalScores:
    scores = EvalScores()
    for r in results:
        if r.got_relevant is None:
            continue
        expected = r.case.expected_relevant
        got = r.got_relevant
        _update_confusion(scores.overall, expected, got)
        if r.case.stratum:
            _update_confusion(
                scores.per_stratum.setdefault(r.case.stratum, StratumScore()),
                expected,
                got,
            )
        if expected and got and r.case.expected_event_type is not None:
            scores.event_type_compared += 1
            if r.got_event_type == r.case.expected_event_type:
                scores.event_type_matched += 1
        if got:
            scores.confidence_counts[r.got_confidence] = (
                scores.confidence_counts.get(r.got_confidence, 0) + 1
            )
        if expected and got and r.case.expected_confidence is not None:
            scores.confidence_expected += 1
            if r.got_confidence == r.case.expected_confidence:
                scores.confidence_matched += 1
    return scores


def is_disagreement(r: CaseResult) -> bool:
    if r.got_relevant is None:
        return False
    if r.got_relevant != r.case.expected_relevant:
        return True
    return bool(
        r.got_relevant
        and r.case.expected_event_type is not None
        and r.got_event_type != r.case.expected_event_type
    )


def format_estimate(
    conn: sqlite3.Connection, cfg: Config, cases: list[EvalCase], backend: str
) -> str:
    cache: dict[int, dict[int, LLMEvent]] = {}
    positives = sum(1 for c in cases if c.expected_relevant)
    images = 0
    chars = 0
    for case in cases:
        if case.job_id not in cache:
            cache[case.job_id] = _events_by_job(conn, case.job_id)
        event = cache[case.job_id].get(case.event_id)
        if event is None:
            continue
        kf_triage = 1 if event.keyframes else 0
        images += kf_triage
        prompt = build_triage_prompt(
            event.event_type,
            event.detector_score,
            event.start_sec,
            event.transcript_window,
            evidence_summary=event.evidence_summary,
        )
        chars += len(prompt)
        if case.expected_relevant:
            kfs = min(len(event.keyframes), DETAIL_KEYFRAMES)
            images += kfs
            prompt = build_detail_prompt(
                event.event_type,
                event.detector_score,
                event.start_sec,
                event.end_sec,
                event.transcript_window,
                tiled=False,
                evidence_summary=event.evidence_summary,
            )
            chars += len(prompt)
    calls = len(cases) + positives
    est_in, est_out = estimate_tokens(chars, images)
    lines = [
        "— eval estimate —",
        f"cases: {len(cases)} ({positives} positive, {len(cases) - positives} negative)",
        f"calls: {len(cases)} triage + {positives} detail = {calls}",
        f"images: {images}",
        f"est tokens: in ~{est_in}, out ~{est_out}",
    ]
    if backend == "cloud":
        cost = call_cost_usd(cfg.cloud, est_in, est_out, images)
        spent = db.cloud_spend_month(conn)
        lines.append(
            f"prices: ${cfg.cloud.price_per_1m_input_tokens:.2f}/1M in, "
            f"${cfg.cloud.price_per_1m_output_tokens:.2f}/1M out, "
            f"${cfg.cloud.price_per_image:.4f}/image"
        )
        lines.append(
            f"est cost: ${cost:.4f}  (month ledger: "
            f"${spent:.4f} of ${cfg.cloud.monthly_budget_usd:.2f} cap)"
        )
    else:
        lines.append("cost: local — $0")
    return "\n".join(lines)


def _expected_str(case: EvalCase) -> str:
    parts = [f"relevant={str(case.expected_relevant).lower()}"]
    if case.expected_event_type is not None:
        parts.append(f"event_type={case.expected_event_type}")
    if case.expected_confidence is not None:
        parts.append(f"confidence={case.expected_confidence}")
    return " ".join(parts)


def format_report(report: EvalReport) -> str:
    s = report.scores.overall
    lines = [
        "— eval report —",
        (
            f"backend: {report.backend}  models: "
            f"triage={report.models['triage']} detail={report.models['detail']}"
        ),
    ]
    n = len(report.results)
    pos = sum(1 for r in report.results if r.case.expected_relevant)
    lines.append(f"cases: {n} ({pos} positive, {n - pos} negative)")
    lines.append(f"confusion: TP {s.tp}  FP {s.fp}  FN {s.fn}  TN {s.tn}")
    lines.append(
        f"precision: {s.precision:.4f}  recall: {s.recall:.4f}  f1: {s.f1:.4f}"
    )
    lines.append(
        f"event-type agreement: {report.scores.event_type_matched}/"
        f"{report.scores.event_type_compared}"
    )
    if report.scores.confidence_counts:
        bands = ", ".join(
            f"{band}={count}"
            for band, count in sorted(report.scores.confidence_counts.items())
        )
        lines.append(f"confidence bands (relevant verdicts): {bands}")
        if report.scores.confidence_expected:
            lines.append(
                f"confidence match vs expected: "
                f"{report.scores.confidence_matched}/{report.scores.confidence_expected}"
            )
    if report.scores.per_stratum:
        lines.append("per-stratum:")
        for name, st in sorted(report.scores.per_stratum.items()):
            lines.append(
                f"  {name}: TP {st.tp} FP {st.fp} FN {st.fn} TN {st.tn}  "
                f"P {st.precision:.3f} R {st.recall:.3f}"
            )
    disagreements = [r for r in report.results if is_disagreement(r)]
    if disagreements:
        lines.append("disagreements:")
        for r in disagreements:
            note = f" — {r.case.note}" if r.case.note else ""
            lines.append(f"  {r.case.label}{note}")
            lines.append(f"    expected: {_expected_str(r.case)}")
            model = report.models.get(r.got_stage, "?")
            lines.append(
                f"    got: relevant={str(r.got_relevant).lower()} "
                f"event_type={r.got_event_type} confidence={r.got_confidence} "
                f"({r.got_stage}, {model})"
            )
    errors = [r for r in report.results if r.got_relevant is None]
    if errors:
        lines.append(f"errors: {len(errors)}")
        for r in errors:
            lines.append(f"  {r.case.label}: {r.error}")
    if report.backend == "cloud":
        u = report.cloud_usage
        lines.append(
            f"cloud usage: {u.calls} calls, {u.images} images, "
            f"in {u.input_tokens} tok, out {u.output_tokens} tok, "
            f"est ${u.est_cost_usd:.4f}"
        )
        lines.append(
            f"ledger this month: ${report.month_spend:.4f}"
        )
    return "\n".join(lines)


def save_baseline(report: EvalReport, path: Path) -> None:
    payload = {
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "backend": report.backend,
        "models": report.models,
        "case_count": len(report.results),
        "scores": report.scores.overall.to_dict(),
        "per_stratum": {
            name: st.to_dict()
            for name, st in sorted(report.scores.per_stratum.items())
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def load_baseline(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise CasesError(f"baseline file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise CasesError(f"baseline JSON parse error in {path}: {e}") from e
    if not isinstance(data, dict):
        raise CasesError(f"baseline file {path} must contain a JSON object")
    return data


def gate_regressed(baseline: dict[str, Any], scores: EvalScores) -> bool:
    base = baseline.get("scores", {})
    base = base if isinstance(base, dict) else {}
    base_p = float(base.get("precision", 0.0))
    base_r = float(base.get("recall", 0.0))
    return (
        scores.overall.precision < base_p - 1e-9
        or scores.overall.recall < base_r - 1e-9
    )


def format_baseline_compare(
    report: EvalReport, baseline: dict[str, Any]
) -> str:
    lines = [
        (
            f"baseline: {baseline.get('date', '?')} "
            f"({baseline.get('backend', '?')}, "
            f"{baseline.get('case_count', '?')} cases)"
        )
    ]
    base_models = baseline.get("models", {})
    if base_models and base_models != report.models:
        lines.append(f"warning: models differ — baseline {base_models} vs current")
    base = baseline.get("scores", {})
    base = base if isinstance(base, dict) else {}
    cur = report.scores.overall
    for metric in ("precision", "recall", "f1"):
        b = float(base.get(metric, 0.0))
        c_val = float(getattr(cur, metric))
        if c_val < b - 1e-9:
            verdict = "regressed"
        elif c_val > b + 1e-9:
            verdict = "improved"
        else:
            verdict = "unchanged"
        lines.append(f"{metric}: {b:.4f} -> {c_val:.4f} ({verdict})")
    base_strata = baseline.get("per_stratum", {})
    base_strata = base_strata if isinstance(base_strata, dict) else {}
    for name in sorted(set(base_strata) | set(report.scores.per_stratum)):
        b_stratum = base_strata.get(name, {})
        b_stratum = b_stratum if isinstance(b_stratum, dict) else {}
        c_stratum = report.scores.per_stratum.get(name)
        cp = c_stratum.precision if c_stratum else 0.0
        cr = c_stratum.recall if c_stratum else 0.0
        lines.append(
            f"stratum {name}: precision "
            f"{float(b_stratum.get('precision', 0.0)):.4f} -> "
            f"{cp:.4f}, recall {float(b_stratum.get('recall', 0.0)):.4f} -> {cr:.4f}"
        )
    return "\n".join(lines)
