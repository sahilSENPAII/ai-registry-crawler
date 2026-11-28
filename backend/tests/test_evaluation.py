import dataclasses

import pytest

from app import cli, evaluation
from app.config import config
from app.evaluation import choice, evaluate, report, scores
from app.llm import FakeLLM, reply, tool_use

TRUTH = {r.name: truth for r, truth, _ in evaluation.dataset()}


def oracle(messages, system, tools):
    """Answers with the ground truth, quoting the last line of what it was shown."""
    text = messages[0]["content"][0]["text"]
    name = text.split("name: ", 1)[1].split("\n", 1)[0]
    is_ai, is_agent = TRUTH[name]
    category = "ai_agent" if is_agent else "genai_application" if is_ai else "none"
    quote = [line.strip() for line in text.splitlines() if line.strip()][-1]
    return tool_use(("record_verdict", {"is_ai": is_ai, "is_agent": is_agent, "category": category,
                                        "reasoning": "ground truth", "evidence_quotes": [quote]}), tokens=(2000, 100))


def test_scores():
    s = scores([(True, True), (True, False), (False, True), (False, False), (True, True)])
    assert (s["tp"], s["fp"], s["fn"], s["tn"]) == (2, 1, 1, 1)
    assert s["precision"] == pytest.approx(2 / 3) and s["recall"] == pytest.approx(2 / 3) and s["f1"] == pytest.approx(2 / 3)
    assert scores([(False, False)])["f1"] == 0.0


@pytest.fixture(scope="module")
def result():
    return evaluate(FakeLLM(oracle, model_id="au.anthropic.claude-haiku-5-5"))


@pytest.fixture(scope="module")
def silent():
    """A model that never gives a verdict, so its hybrid is the rules."""
    return evaluate(FakeLLM(lambda messages, system, tools: reply("Not sure."), model_id="au.anthropic.claude-sonnet-5-5"))


def test_evaluation_scores_each_method(result):
    m = result["metrics"]
    assert len(result["rows"]) == 41
    assert m["llm"]["is_ai"]["f1"] == m["llm"]["is_agent"]["f1"] == 1.0
    assert m["rules"]["is_ai"]["recall"] < m["hybrid"]["is_ai"]["recall"] < 1.0  # sklearn-scorer: confidently wrong
    assert sum(map(sum, result["confusion"]["rules"])) == 41
    calls, _, _, cost = result["usage"]["llm"]
    assert calls == 41 and cost == pytest.approx(41 * (2000 * 0.10 + 100 * 0.50) / 1_000_000)
    assert result["usage"]["hybrid"][0] == sum(not r["confident"] for r in result["rows"]) < 41


def test_report_compares_models(result, silent):
    md = report([result, silent])
    for heading in ["## Where the models ran", "## Scores", "## Cases sent to the LLM and cost", "## Model choice", "## Confusion",
                    "## Hybrid decisions", "## Where a method was wrong"]:
        assert heading in md
    assert "| Resource | Why it is hard | Truth | rules | llm haiku-5-5 | hybrid haiku-5-5 | llm sonnet-5-5 | " \
           "hybrid sonnet-5-5 |" in md
    assert "| sklearn-scorer | serves a pickled scikit-learn model with no AI keywords | AI, not agent |" in md
    unsure = sum(not r["confident"] for r in result["rows"])
    assert f"| hybrid sonnet-5-5 | {unsure} | {unsure} | " in md  # every missing verdict counts as rejected


def test_another_model_must_be_clearly_better_at_agents(result, silent):
    gain = result["metrics"]["hybrid"]["is_agent"]["f1"] - silent["metrics"]["hybrid"]["is_agent"]["f1"]
    assert gain >= 0.05
    assert choice([silent, result]) == result["model"]
    assert choice([result, silent]) == choice([result, result]) == result["model"]


def test_evaluate_keeps_the_hand_written_history(tmp_path, monkeypatch):
    path = tmp_path / "evaluation.md"
    path.write_text("# old report\n\n## Validator history\n\nwritten by hand\n")
    monkeypatch.setattr(cli, "REPORT", path)
    monkeypatch.setattr(cli, "config", dataclasses.replace(config, llm_eval_models=["au.anthropic.claude-haiku-5-5"]))
    monkeypatch.setattr(cli, "make_llm", lambda session, model: FakeLLM(oracle, model_id=model))
    cli.evaluate()
    md = path.read_text()
    assert "# old report" not in md and "## Scores, validator v2" in md
    assert md.endswith("\n## Validator history\n\nwritten by hand\n")


def test_report_names_models_that_could_not_run(result):
    md = report([result], {"gemini-3.1-pro-preview": "RuntimeError: Gemini 429 RESOURCE_EXHAUSTED: limit: 0"})
    assert "No other model was evaluated, so the default stays." in md
    assert "`gemini-3.1-pro-preview` could not be evaluated: RuntimeError: Gemini 429 RESOURCE_EXHAUSTED: limit: 0" in md
