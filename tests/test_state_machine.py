"""Structural validation of the Step Functions definition (infra/terraform/pipeline.asl.json.tpl)."""
import json
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parents[1] / "infra" / "terraform" / "pipeline.asl.json.tpl"
TERMINAL = {"Succeed", "Fail"}


@pytest.fixture(scope="module")
def machine():
    text = (
        TEMPLATE.read_text()
        .replace("${glue_job_name}", "finpipe-dev-transform")
        .replace("${gate_lambda_arn}", "arn:aws:lambda:us-east-1:123456789012:function:gate")
        .replace("${alert_topic_arn}", "arn:aws:sns:us-east-1:123456789012:alerts")
    )
    assert "${" not in text, "unrendered template variable"
    return json.loads(text)


def targets(state: dict):
    out = [state["Next"]] if "Next" in state else []
    out += [catch["Next"] for catch in state.get("Catch", [])]
    out += [choice["Next"] for choice in state.get("Choices", [])]
    if "Default" in state:
        out.append(state["Default"])
    return out


def test_start_state_exists(machine):
    assert machine["StartAt"] in machine["States"]


def test_every_transition_points_at_an_existing_state(machine):
    for name, state in machine["States"].items():
        for target in targets(state):
            assert target in machine["States"], f"{name} -> {target}"


def test_every_state_is_reachable_and_every_path_terminates(machine):
    states = machine["States"]
    seen, stack = set(), [machine["StartAt"]]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(targets(states[current]))
    assert seen == set(states), f"unreachable: {set(states) - seen}"

    def terminates(name, path=()):
        assert name not in path, f"cycle through {name}"
        state = states[name]
        if state["Type"] in TERMINAL:
            return True
        return all(terminates(t, path + (name,)) for t in targets(state))

    assert terminates(machine["StartAt"])


def test_both_a_success_and_a_failure_ending_exist(machine):
    kinds = {s["Type"] for s in machine["States"].values()}
    assert TERMINAL <= kinds


def test_every_worker_task_routes_errors_to_the_failure_alert(machine):
    for name in ("Transform", "QualityGate"):
        catches = machine["States"][name]["Catch"]
        assert any("States.ALL" in c["ErrorEquals"] and c["Next"] == "NotifyFailure" for c in catches), name


def test_glue_retries_wait_for_the_running_job_and_retry_a_failure_only_once(machine):
    retries = {tuple(r["ErrorEquals"]): r for r in machine["States"]["Transform"]["Retry"]}
    assert retries[("Glue.ConcurrentRunsExceededException",)]["MaxAttempts"] >= 10
    assert retries[("States.TaskFailed",)]["MaxAttempts"] == 1


def test_run_id_is_the_execution_name_for_both_the_job_and_the_gate(machine):
    job_args = machine["States"]["Transform"]["Parameters"]["Arguments"]
    assert job_args["--run_id.$"] == "$$.Execution.Name"
    assert job_args["--dt.$"] == "$.dt"
    assert machine["States"]["QualityGate"]["Parameters"]["Payload"]["run_id.$"] == "$$.Execution.Name"


def test_quality_choice_sends_degraded_runs_to_an_alert_but_still_finishes(machine):
    states = machine["States"]
    assert states["QualityChoice"]["Default"] == "NotifyDegraded"
    assert states["NotifyDegraded"]["Next"] == "Done"
    assert states["Done"]["Type"] == "Succeed"
    assert states["Failed"]["Type"] == "Fail"


def test_notifications_do_not_overwrite_the_state_input(machine):
    for name in ("NotifyDegraded", "NotifyFailure"):
        assert machine["States"][name]["ResultPath"] is None
