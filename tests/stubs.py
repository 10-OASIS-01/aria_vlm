"""Test doubles shared by unit and integration tests."""

import time

from aria_guidance.engine import Generation, parse_output


class StubEngine:
    """Scripted model. Set `reply` to raw model text or an Exception, and `delay` in seconds."""

    def __init__(self, reply="Pick up the pen.", delay=0.0):
        self.state, self.reply, self.delay = "ready", reply, delay
        self.busy = False
        self.calls = []
        self.intent = "done"  # classify's label, or an Exception.
        self.intent_calls = []

    def classify(self, text, goal, state, instruction):
        self.intent_calls.append(
            {"text": text, "goal": goal, "state": state, "instruction": instruction}
        )
        if isinstance(self.intent, Exception):
            raise self.intent
        return {
            "label": self.intent,
            "raw_output": str(self.intent),
            "input_tokens": 1,
            "output_tokens": 1,
            "timings": {},
        }

    def status(self):
        return {"state": self.state, "busy": self.busy}

    def generate(self, goal, history, images, timestamps):
        self.calls.append({"goal": goal, "history": list(history), "timestamps": timestamps})
        self.busy = True
        try:
            time.sleep(self.delay)
            if isinstance(self.reply, Exception):
                raise self.reply
            instruction, status, proposal = parse_output(self.reply, goal)
            return Generation(instruction, status, self.reply, {}, 1, 1, proposal)
        finally:
            self.busy = False
