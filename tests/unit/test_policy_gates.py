from policy import gates
from policy.models import CreatedBy, Policy, ScopeLevel


def _policy(topic="t", ears_text="the system shall x"):
    return Policy(
        id="p1", topic=topic, target_pillar="deployment", ears_text=ears_text,
        test_script="def test_x():\n    pass\n", scope_level=ScopeLevel.ALL_PROJECTS, created_by=CreatedBy.USER,
    )


# -- Gate 1: EARS check -------------------------------------------------------


def test_gate1_recognizes_already_ears_compliant_text():
    result = gates.gate1_ears_check(lambda p: "EARS_OK: WHEN x, the system shall y.", "WHEN x, the system shall y.")
    assert result.refined is False
    assert result.ears_text == "WHEN x, the system shall y."


def test_gate1_uses_the_refined_text_when_not_ears():
    result = gates.gate1_ears_check(lambda p: "EARS_REFINED: WHEN x, the system shall y.", "make it do y when x")
    assert result.refined is True
    assert result.ears_text == "WHEN x, the system shall y."


def test_gate1_treats_an_unparseable_response_as_a_refinement():
    result = gates.gate1_ears_check(lambda p: "sure, here's a rewrite: the system shall y", "do y")
    assert result.refined is True
    assert result.ears_text == "sure, here's a rewrite: the system shall y"


# -- Gate 2: enforceability ---------------------------------------------------


def test_gate2_parses_yes():
    result = gates.gate2_enforceability_check(lambda p: "ENFORCEABLE: yes", "the system shall x")
    assert result.enforceable is True


def test_gate2_parses_no_with_reason():
    result = gates.gate2_enforceability_check(lambda p: "ENFORCEABLE: no - requires subjective human judgment", "x")
    assert result.enforceable is False
    assert "subjective" in result.reason


def test_gate2_fails_closed_on_an_unparseable_response():
    result = gates.gate2_enforceability_check(lambda p: "I'm not sure about this one", "x")
    assert result.enforceable is False
    assert "could not parse" in result.reason


# -- Gate 3b: semantic conflict ------------------------------------------------


def test_gate3b_no_existing_policies_means_no_conflict_without_calling_claude():
    calls = []

    def query(prompt):
        calls.append(prompt)
        return "should never be called"

    result = gates.gate3b_conflict_check(query, "the system shall x", [])
    assert result.conflicts is False
    assert calls == []


def test_gate3b_parses_no_conflict():
    result = gates.gate3b_conflict_check(lambda p: "NO_CONFLICT", "the system shall x", [_policy()])
    assert result.conflicts is False


def test_gate3b_parses_a_real_conflict():
    result = gates.gate3b_conflict_check(
        lambda p: "CONFLICT: server-health - directly contradicts the existing rule", "the system shall never x",
        [_policy(topic="server-health")],
    )
    assert result.conflicts is True
    assert result.conflicting_topic == "server-health"
    assert "contradicts" in result.detail


def test_gate3b_unparseable_response_is_treated_as_no_conflict():
    result = gates.gate3b_conflict_check(lambda p: "hmm, hard to say", "x", [_policy()])
    assert result.conflicts is False


def test_gate3b_includes_every_existing_policy_in_the_prompt():
    captured = {}

    def query(prompt):
        captured["prompt"] = prompt
        return "NO_CONFLICT"

    gates.gate3b_conflict_check(query, "new requirement", [_policy(topic="a", ears_text="shall a"), _policy(topic="b", ears_text="shall b")])
    assert "[a] shall a" in captured["prompt"]
    assert "[b] shall b" in captured["prompt"]


# -- script generation ---------------------------------------------------------


def test_generate_test_script_returns_raw_response():
    script = gates.generate_test_script(lambda p: "def test_x():\n    assert True\n", "the system shall x", project_bound=True)
    assert script == "def test_x():\n    assert True"


def test_generate_test_script_strips_a_markdown_code_fence():
    response = "```python\ndef test_x():\n    assert True\n```"
    script = gates.generate_test_script(lambda p: response, "the system shall x", project_bound=True)
    assert script == "def test_x():\n    assert True"


def test_generate_test_script_notes_project_bound_vs_framework_bound_in_the_prompt():
    captured = {}

    def query(prompt):
        captured["prompt"] = prompt
        return "def test_x(): pass"

    gates.generate_test_script(query, "x", project_bound=False)
    assert "framework-bound" in captured["prompt"]

    gates.generate_test_script(query, "x", project_bound=True)
    assert "project-bound" in captured["prompt"]


def test_generate_test_script_states_the_real_sandbox_mount_paths():
    """A real M13 catalog run against the real Claude CLI produced a
    script that guessed at LWS_PROJECT_ROOT/an upward .git walk from
    cwd — neither exists in the real sandbox (cwd is /policy, the
    checkout is bind-mounted at the fixed path /project) — so the
    script silently found no VERSION file even for a compliant project.
    The prompt must state the real, fixed mount paths explicitly rather
    than leaving Claude to guess a resolution convention that doesn't
    exist."""
    captured = {}

    def query(prompt):
        captured["prompt"] = prompt
        return "def test_x(): pass"

    gates.generate_test_script(query, "x", project_bound=True)
    assert "/project" in captured["prompt"]
    assert "/project-files" in captured["prompt"]
    assert "environment variable" in captured["prompt"]

    gates.generate_test_script(query, "x", project_bound=False)
    assert "/framework/src" in captured["prompt"]
    assert "/framework/docs" in captured["prompt"]


def test_gate1_extracts_the_marker_even_when_the_model_adds_preamble_first():
    response = "Wait — this is already valid EARS state-driven form.\n\nEARS_OK: WHILE x, the system shall y."
    result = gates.gate1_ears_check(lambda p: response, "WHILE x, the system shall y.")
    assert result.refined is False
    assert result.ears_text == "WHILE x, the system shall y."


def test_gate1_uses_the_last_marker_when_the_model_second_guesses_itself():
    response = "EARS_REFINED: draft one\n\nActually, EARS_OK: the system shall y."
    result = gates.gate1_ears_check(lambda p: response, "do y")
    assert result.refined is False
    assert result.ears_text == "the system shall y."
