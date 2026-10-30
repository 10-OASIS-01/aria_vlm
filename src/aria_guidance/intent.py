"""What open-mic speech asks for: wake word, start phrase, echo, and classifier labels. No ROS."""

import re

from .task import CANCEL_WORDS, NEXT_WORDS, parse_reply

LABELS = {"done", "help", "correction", "repeat", "cancel", "finish", "ignore"}
# Whisper also writes the name as these. It must come first, so "in that area" does not count.
WAKE = re.compile(
    r"^\W*(?:(?:okay|ok|hey|hi|um|uh)\W+)?(?:aria|arya|area|maria)(?![\w'’])\W*(.*)$", re.I
)
START = re.compile(
    r"^\W*(?:(?:trial|goal|task)\W+start(?:ed|s|ing)?"
    r"|start(?:ed)?\W+(?:(?:the|a)\W+)?(?:new\W+)?(?:trial|goal|task))(?!\w)\W*(.*)$",
    re.I,
)
SAY_CANCEL = "Say Aria, stop to cancel."
SAY_FINISH = "Say Aria, finish to finish."
SAY_CONFIRM = "Say done to confirm."
SAY_START = "No trial running. Say Aria, trial start."


def normalized(text):
    return re.sub(r"[\W_]+", " ", text).strip().lower()


def strip_wake(text):
    """Returns (addressed, the words after the wake word); the whole text when not addressed."""
    match = WAKE.match(text.strip())
    return (True, match[1].strip()) if match else (False, text.strip())


def match_start(text):
    """The goal after a start phrase ("" for the phrase alone), or None without one."""
    match = START.match(text)
    return match[1].strip() if match else None


def is_echo(text, last_tts):
    """True when unaddressed speech of three or more words appears in order in the last speech.

    Short replies such as "done" still count, and so does anything starting with "Aria": the hints
    ("Say Aria, trial start.") invite exactly those words.
    """
    words = normalized(text)
    return (
        not strip_wake(text)[0]
        and len(words.split()) >= 3
        and f" {words} " in f" {normalized(last_tts or '')} "
    )


def interpret(text, task, window=None):
    """Maps one transcript to a command, before any model call.

    Args:
        text: what the recognizer heard.
        task: the current Task.
        window: the open reply window: goal (the next words are the goal), reply (the system asked
            something) or aria (a bare "Aria" came just before, so this is addressed), or None.

    Returns:
        (command, text): discard (not for the system), none (heard, nothing to do), listen,
        ask_goal, start, say, classify, or parse_reply's cancel, repeat, finish, confirm, note and
        feedback. Only exact words can close a trial.
    """
    if not text.strip():
        return "discard", ""
    said_aria, rest = strip_wake(text)
    addressed = said_aria or window == "aria"
    if said_aria and not rest:
        return "listen", ""
    if not addressed and window is None:
        return "discard", ""
    # A stale reply window must not reach the idle hint: nobody asked for this speech.
    if not addressed and window == "reply" and not task.active:
        return "discard", ""
    word = normalized(rest)
    if window == "goal":
        # "Stop" in answer to "What is the goal?" drops the question; it is not a goal.
        if word in CANCEL_WORDS:
            return "none", rest
        goal = match_start(rest)
        if goal == "":
            return "ask_goal", ""
        return "start", rest if goal is None else goal
    if addressed:
        goal = match_start(rest)
        if goal is not None:
            return ("start", goal) if goal else ("ask_goal", "")
    if not task.active:
        return "say", SAY_START
    command, reply = parse_reply(rest, task)
    if command in {"cancel", "finish"}:
        return (command, reply) if addressed else ("discard", "")
    if command == "goal":  # "goal: ...", which must not reach the classifier and then reply().
        return ("start", reply) if addressed else ("discard", "")
    if command in {"repeat", "confirm", "note"}:
        return command, reply
    if word in NEXT_WORDS:
        return "feedback", reply
    return "classify", rest


def parse_label(raw):
    """The classifier's label, or None when its answer is not one."""
    words = normalized(raw).split()
    return words[0] if words and words[0] in LABELS else None


def route(label, text, task):
    """Maps a classifier label to a command. It never closes a trial, whatever the model said.

    Args:
        label: one of LABELS, or None for an unusable answer.
        text: the words classified.
        task: the current Task.

    Returns:
        (command, text): feedback, repeat, say or none.
    """
    if label in {"help", "correction"}:
        return "feedback", text
    if label == "done":
        return ("say", SAY_CONFIRM) if task.state == "awaiting_confirmation" else ("feedback", text)
    if label == "repeat":
        return "repeat", text
    if label == "cancel":
        return "say", SAY_CANCEL
    if label == "finish":
        return "say", SAY_FINISH
    return "none", text
