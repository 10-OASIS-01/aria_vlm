import pytest

from aria_guidance.engine import parse_output

GOAL = "Put the orange pen into the green mug."


@pytest.mark.parametrize("raw", ["DONE", "done.", "Done!"])
def test_done_only_proposes_completion(raw):
    assert parse_output(raw, GOAL)[:2] == ("The task looks complete.", "done_proposed")


def test_sentence_mentioning_done_is_an_instruction():
    text = "Place the pen in the mug and you are done."
    assert parse_output(text, GOAL)[:2] == (text, "instruction")


def test_ask_prefix_is_a_clarification():
    expected = ("Which mug is green?", "clarification")
    assert parse_output("ASK: Which mug is green?", GOAL)[:2] == expected


def test_empty_clarification_is_rejected():
    with pytest.raises(ValueError, match="empty clarification"):
        parse_output("ASK:", GOAL)


@pytest.mark.parametrize("raw", ["ASK: Put the orange pen into the green mug?", f"{GOAL[:-1]}?"])
def test_goal_echoed_as_question_is_rejected(raw):
    with pytest.raises(ValueError, match="echoed the goal"):
        parse_output(raw, GOAL)


def test_goal_repeated_as_instruction_is_allowed():
    assert parse_output(GOAL, GOAL)[:2] == (GOAL, "instruction")


def test_long_output_is_not_spoken():
    with pytest.raises(ValueError, match="too long"):
        parse_output(" ".join(["word"] * 61), GOAL)


@pytest.mark.parametrize("raw", ["", "<think>planning", "<think>planning</think>"])
def test_reasoning_or_empty_output_is_rejected(raw):
    with pytest.raises(ValueError, match="no usable instruction"):
        parse_output(raw, GOAL)


def test_closed_reasoning_is_stripped():
    assert parse_output("<think>scene</think>Pick up the pen.", GOAL)[:2] == (
        "Pick up the pen.",
        "instruction",
    )


FOUR_LINES = """EVIDENCE: The orange pen lies on the white mat, left of the green mug.
MODE: ACT
SAY: Pick up the orange pen on your left with your right hand and drop it into the green mug.
EXPECT: The orange pen is inside the green mug."""


def test_four_lines_speak_only_say():
    text, status, proposal = parse_output(FOUR_LINES, GOAL)
    assert text.startswith("Pick up the orange pen on your left") and status == "instruction"
    assert proposal == {
        "mode": "ACT",
        "evidence": "The orange pen lies on the white mat, left of the green mug.",
        "expect": "The orange pen is inside the green mug.",
    }


@pytest.mark.parametrize(
    "mode, status",
    [("INSPECT", "instruction"), ("REPLAN", "instruction"), ("ASK", "clarification")],
)
def test_modes_map_to_status(mode, status):
    raw = f"EVIDENCE: Rack is hidden.\nMODE: {mode}\nSAY: Is the lower rack full?\nEXPECT: -"
    proposal = {"mode": mode, "evidence": "Rack is hidden.", "expect": "-"}
    assert parse_output(raw, GOAL)[1:] == (status, proposal)


def test_markdown_tags_and_lowercase_mode_are_read():
    raw = "**EVIDENCE**: Pen in mug.\n**MODE**: done.\n**SAY**: The orange pen is in the green mug."
    assert parse_output(raw, GOAL)[:2] == ("The orange pen is in the green mug.", "done_proposed")


def test_done_without_say_keeps_the_fixed_sentence():
    raw = "EVIDENCE: Pen in mug.\nMODE: DONE\nEXPECT: Nothing left."
    assert parse_output(raw, GOAL)[:2] == ("The task looks complete.", "done_proposed")


@pytest.mark.parametrize(
    "raw, message",
    [
        ("EVIDENCE: Pen.\nMODE: GRAB\nSAY: Grab it.", "unknown mode"),
        ("EVIDENCE: Pen.\nMODE: ACT\nEXPECT: Pen in mug.", "no SAY line"),
        (f"EVIDENCE: Pen.\nMODE: ASK\nSAY: {GOAL[:-1]}?", "echoed the goal"),
    ],
)
def test_bad_four_lines_are_rejected(raw, message):
    with pytest.raises(ValueError, match=message):
        parse_output(raw, GOAL)


def test_one_line_answer_has_no_evidence():
    proposal = parse_output("Pick up the pen.", GOAL)[2]
    assert proposal == {"mode": "ACT", "evidence": "", "expect": ""}
