"""One guidance task: goal, dialogue history and step state. No ROS, no HTTP.

States: idle -> ready (a plan is wanted) -> planning -> awaiting_feedback or awaiting_confirmation.
Feedback returns to ready; confirm (after a completion proposal) or finish (any time, by the
operator) closes as completed; cancel closes as cancelled. A failed plan
moves to error, and the next reply plans again. Not thread-safe: the node calls it from one thread.
"""

CONFIRM_WORDS = {"done", "yes", "confirm", "完成", "好了", "是"}
NEXT_WORDS = {"next", "done", "完成", "下一步", "好了"}
CANCEL_WORDS = {"cancel", "stop", "停止", "取消"}
REPEAT_WORDS = {"repeat", "重复", "再说一遍"}
FINISH_WORDS = {"finish", "finished", "finish task", "task finished", "结束"}
# Saying so explicitly stops the model from repeating DONE (a bare "Next." did not, in replay).
REJECTION = "No, the goal is not complete yet. "
MAX_HISTORY = 24


def parse_reply(text, task):
    """Maps an operator reply or transcript to a command.

    Args:
        text: typed or spoken reply.
        task: the current Task, for the words whose meaning depends on its state.

    Returns:
        (command, text): command is goal, note, cancel, repeat, finish, confirm, feedback or none.
    """
    text = text.strip()
    word = text.lower().rstrip(".!?。！？")
    if word in CANCEL_WORDS:
        return "cancel", text
    if word in REPEAT_WORDS:
        return "repeat", text
    if word in FINISH_WORDS and task.active:
        return "finish", text
    for prefix in ("goal", "note"):
        if word.startswith(prefix + ":"):
            return prefix, text[len(prefix) + 1 :].strip()
    if word in CONFIRM_WORDS and task.state == "awaiting_confirmation":
        return "confirm", text
    if not text or (word in NEXT_WORDS | FINISH_WORDS and not task.active):
        return "none", text  # "next" or "finish" without a task is not a goal.
    return ("feedback", text) if task.active else ("goal", text)


class Task:
    def __init__(self):
        self.goal, self.history, self.state = None, [], "idle"
        self.epoch = 0  # Bumped by start and cancel; results from an older epoch are dropped.
        self.last = None  # Last accepted plan result, for repeat.

    @property
    def active(self):
        return self.state not in {"idle", "completed", "cancelled"}

    def start(self, goal):
        self.epoch += 1
        self.goal, self.history, self.state, self.last = goal, [], "ready", None

    def cancel(self):
        self.epoch += 1
        self.state = "cancelled"

    def begin_plan(self):
        """Returns (epoch, goal, history) for one plan request, or None if no plan is wanted."""
        if self.state != "ready":
            return None
        self.state = "planning"
        return self.epoch, self.goal, list(self.history)

    def finish_plan(self, epoch, result):
        """Accepts a plan result; returns False when the task moved on while it ran."""
        if epoch != self.epoch or self.state != "planning":
            return False
        done = result["status"] == "done_proposed"
        self.state = "awaiting_confirmation" if done else "awaiting_feedback"
        self.add("assistant", result["instruction"])
        self.last = result
        return True

    def fail_plan(self, epoch):
        if epoch == self.epoch and self.state == "planning":
            self.state = "error"

    def feedback(self, text):
        """Records a reply and asks for a new plan; returns False while one is running."""
        if self.state not in {"ready", "awaiting_feedback", "awaiting_confirmation", "error"}:
            return False
        if self.state == "awaiting_confirmation":
            text = REJECTION + text
        self.add("user", text)
        self.state = "ready"
        return True

    def confirm(self):
        if self.state != "awaiting_confirmation":
            return False
        self.state = "completed"
        return True

    def finish(self):
        """The operator declares the goal reached, with or without a proposal from the model."""
        if not self.active:
            return False
        self.epoch += 1  # A plan still running must not speak after the task closed.
        self.state = "completed"
        return True

    def add(self, role, text):
        self.history = (self.history + [{"role": role, "text": text}])[-MAX_HISTORY:]
