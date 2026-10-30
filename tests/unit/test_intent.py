import json
from pathlib import Path

import pytest

from aria_guidance.intent import (
    LABELS,
    SAY_CANCEL,
    SAY_CONFIRM,
    SAY_FINISH,
    SAY_START,
    interpret,
    is_echo,
    match_start,
    parse_label,
    route,
    strip_wake,
)
from aria_guidance.task import Task


def running(status="instruction"):
    task = Task()
    task.start("Clear the mat")
    epoch, _, _ = task.begin_plan()
    task.finish_plan(epoch, {"status": status, "instruction": "Pick up the pen."})
    return task


@pytest.mark.parametrize(
    "text, rest",
    [
        ("Aria, what's next", "what's next"),
        ("aria. stop.", "stop."),
        ("Okay Aria, what's next?", "what's next?"),
        ("Hey, Arya, done", "done"),
        ("Um, Maria, repeat", "repeat"),
        ("Area, next", "next"),
        ("Aria", ""),
        ("Aria.", ""),
    ],
)
def test_wake_word_addresses_the_system(text, rest):
    assert strip_wake(text) == (True, rest)


@pytest.mark.parametrize(
    "text",
    [
        "In that area, put the cup",
        "I said Aria stop",
        "Okay let's go",
        "Arias are nice",
        "Aria's phone rang",
        "Area's fine",
        "Maria's cup is there",
        "",
    ],
)
def test_other_speech_is_not_addressed(text):
    assert strip_wake(text)[0] is False


@pytest.mark.parametrize(
    "text, goal",
    [
        ("trial start, pick up the marker", "pick up the marker"),
        ("Trial started.", ""),
        ("goal start", ""),
        ("Task start: stack the cups.", "stack the cups."),
        ("start the trial", ""),
        ("Start a new task, clear the mat", "clear the mat"),
    ],
)
def test_start_phrase(text, goal):
    assert match_start(text) == goal


@pytest.mark.parametrize(
    "text", ["trial end", "start over", "the trial start", "restart the trial"]
)
def test_not_a_start_phrase(text):
    assert match_start(text) is None


def test_echo_needs_three_words_found_in_the_last_speech():
    said = "Say done to confirm, or next to continue."
    assert is_echo("Or next to continue.", said)
    assert not is_echo("done", said)  # A real reply to the prompt.
    assert not is_echo("say something else", said)
    assert not is_echo("Or next to continue.", None)


@pytest.mark.parametrize("hint", [SAY_START, SAY_CANCEL, SAY_FINISH])
def test_doing_what_a_hint_says_is_not_an_echo(hint):
    assert not is_echo(hint.split("Say ")[1], hint)


# (heard, window, expected) for a task that is not running.
@pytest.mark.parametrize(
    "text, window, expected",
    [
        ("pick up the marker", None, ("discard", "")),
        ("", None, ("discard", "")),
        ("in that area", None, ("discard", "")),
        ("Aria, trial start, pick up the marker", None, ("start", "pick up the marker")),
        ("Aria, trial start", None, ("ask_goal", "")),
        ("Aria", None, ("listen", "")),
        ("Aria, what's next", None, ("say", SAY_START)),
        ("pick up the marker", "goal", ("start", "pick up the marker")),
        ("Trial start, pick up the marker", "goal", ("start", "pick up the marker")),
        ("Aria, pick up the marker", "goal", ("start", "pick up the marker")),
        ("trial start", "goal", ("ask_goal", "")),
        ("stop", "goal", ("none", "stop")),
        ("Aria", "goal", ("listen", "")),
        ("thanks", "reply", ("discard", "")),  # A stale window gets no idle hint.
        ("what's next", "aria", ("say", SAY_START)),
        ("trial start, pick up the pen", "aria", ("start", "pick up the pen")),
    ],
)
def test_interpret_while_idle(text, window, expected):
    assert interpret(text, Task(), window) == expected


# (heard, window, expected) while the model's last answer was a plain instruction.
@pytest.mark.parametrize(
    "text, window, expected",
    [
        ("hand me the pen", None, ("discard", "")),
        ("stop", None, ("discard", "")),
        ("Aria, stop.", None, ("cancel", "stop.")),
        ("Aria, cancel", None, ("cancel", "cancel")),
        ("Aria, finish", None, ("finish", "finish")),
        ("Aria, trial end.", None, ("finish", "trial end.")),
        ("Aria, repeat", None, ("repeat", "repeat")),
        ("Aria, next.", None, ("feedback", "next.")),
        ("Aria, done", None, ("feedback", "done")),
        ("Aria, note: it slipped", None, ("note", "it slipped")),
        ("Aria, goal: stack the cups", None, ("start", "stack the cups")),
        ("Aria, trial start, stack the cups", None, ("start", "stack the cups")),
        ("Aria, I can't find it", None, ("classify", "I can't find it")),
        ("Aria, forget it", None, ("classify", "forget it")),
        ("I can't find it", "aria", ("classify", "I can't find it")),
        ("the blue one", "reply", ("classify", "the blue one")),
        ("stop", "reply", ("discard", "")),
        ("finish", "reply", ("discard", "")),
        ("goal: stack the cups", "reply", ("discard", "")),
        ("stop", "aria", ("cancel", "stop")),
        ("finish", "aria", ("finish", "finish")),
    ],
)
def test_interpret_during_a_task(text, window, expected):
    assert interpret(text, running(), window) == expected


@pytest.mark.parametrize("text", ["done", "Yes.", "Aria, done", "Aria, yes."])
def test_exact_words_confirm_a_completion_proposal(text):
    window = None if text.startswith("Aria") else "reply"
    task = running("done_proposed")
    assert interpret(text, task, window)[0] == "confirm"


def test_unaddressed_words_do_not_confirm_after_the_window():
    assert interpret("done", running("done_proposed"), None) == ("discard", "")


@pytest.mark.parametrize(
    "raw, label",
    [
        ("done", "done"),
        ("Correction.", "correction"),
        (" IGNORE ", "ignore"),
        ("help: they are stuck", "help"),
        ("finish", "finish"),
        ("maybe", None),
        ("", None),
        ("I think done", None),
    ],
)
def test_parse_label(raw, label):
    assert parse_label(raw) == label


@pytest.mark.parametrize("state", ["instruction", "done_proposed"])
@pytest.mark.parametrize("label", sorted(LABELS) + [None, "anything"])
def test_a_model_label_never_closes_a_trial(label, state):
    command, _ = route(label, "words", running(state))
    assert command in {"feedback", "repeat", "say", "none"}


def test_route_maps_labels():
    task = running()
    assert route("done", "got it", task) == ("feedback", "got it")
    assert route("help", "how", task) == ("feedback", "how")
    assert route("correction", "the blue one", task) == ("feedback", "the blue one")
    assert route("repeat", "again", task) == ("repeat", "again")
    assert route("cancel", "forget it", task) == ("say", SAY_CANCEL)
    assert route("finish", "that's all", task) == ("say", SAY_FINISH)
    assert route("ignore", "hold on", task) == ("none", "hold on")


def test_done_label_cannot_confirm_a_proposal():
    assert route("done", "looks good", running("done_proposed")) == ("say", SAY_CONFIRM)


def test_eval_cases_are_labelled_and_reach_the_classifier():
    path = Path(__file__).parents[2] / "scripts" / "intent_cases.json"
    cases = json.loads(path.read_text())
    assert {c["label"] for c in cases} == LABELS and len(cases) >= 40
    for case in cases:
        task = running(case.get("status", "instruction"))
        assert interpret("Aria, " + case["text"], task)[0] == "classify", case["text"]
