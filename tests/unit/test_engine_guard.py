"""QwenEngine's repeat guard with the model calls stubbed: when it re-plans, and what it records."""

from aria_guidance.config import Settings
from aria_guidance.engine import REPEAT_FALLBACK, Generation, QwenEngine

LAST = "Put the pen in the cup."
HISTORY = [{"role": "assistant", "text": LAST}, {"role": "user", "text": "No, the other cup."}]


def answer(say, status="instruction"):
    return Generation(say, status, f"SAY: {say}", {"model_ms": 1.0}, 1, 1, {"mode": "ACT"})


class Engine(QwenEngine):
    """Scripted answers; the guard logic is the real one."""

    def __init__(self, replans, label="correction"):
        super().__init__(Settings())
        self.state, self.replans, self.label = "ready", list(replans), label
        self.notes, self.classified = [], 0

    def _generate(self, goal, history, images, timestamps, note=""):
        self.notes.append(note)
        return self.replans.pop(0)

    def _same_step(self, a, b):
        return a == b

    def _classify(self, text, goal, state, instruction):
        self.classified += 1
        return {"label": self.label}


def test_corrected_step_given_again_is_replanned():
    engine = Engine([answer(LAST), answer("Which cup do you mean?")])
    g = engine.generate("sort the pens", HISTORY, [], [])
    assert g.instruction == "Which cup do you mean?"
    assert g.proposal["guard"] == "repeat" and g.proposal["replaced"] == LAST
    assert engine.notes[0] == "" and "No, the other cup." in engine.notes[1]


def test_request_for_help_keeps_the_same_step():
    engine = Engine([answer(LAST)], label="help")
    g = engine.generate("sort the pens", HISTORY, [], [])
    assert g.instruction == LAST and "guard" not in g.proposal and len(engine.notes) == 1


def test_step_repeated_after_the_replan_becomes_a_question():
    engine = Engine([answer(LAST), answer(LAST)])
    g = engine.generate("sort the pens", HISTORY, [], [])
    assert (g.instruction, g.status) == (REPEAT_FALLBACK, "clarification")
    assert g.proposal["replaced"] == LAST


def test_new_step_skips_the_classifier():
    engine = Engine([answer("Put the pen in the blue cup.")])
    g = engine.generate("sort the pens", HISTORY, [], [])
    assert g.instruction == "Put the pen in the blue cup." and engine.classified == 0


def test_first_plan_has_nothing_to_guard():
    engine = Engine([answer(LAST)])
    assert engine.generate("sort the pens", [], [], []).instruction == LAST
