"""What open-mic speech asks for: wake word, start phrase, echo, and classifier labels. No ROS."""

import re

from .task import CANCEL_WORDS, NEXT_WORDS, REPEAT_WORDS, parse_reply

LABELS = {"done", "help", "correction", "repeat", "cancel", "finish", "ignore"}
# Whisper also writes the name as these. It must come first, so "in that area" does not count.
WAKE = re.compile(
    r"^\W*(?:(?:okay|ok|hey|hi|um|uh)\W+)?(?:aria|arya|area|maria)(?![\w'’])\W*(.*)$", re.I
)
# "Next. Aria, next": up to two words first. It can't close a trial, so a mention can't either.
# Not "area" or "maria" here: those are too likely in ordinary speech.
SOFT_WAKE = re.compile(r"^\W*(?:[\w'’]+\W+){1,2}(?:aria|arya)(?![\w'’])\W*(.*)$", re.I)
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


def soft_command(rest, task):
    """The command for speech with "Aria" just after its first words: nothing that ends a trial."""
    if not rest:
        return "discard", ""
    if not task.active:
        return "say", SAY_START
    command, reply = parse_reply(rest, task)
    if command in {"repeat", "note"}:
        return command, reply
    if normalized(rest) in NEXT_WORDS and command != "confirm":
        return "feedback", reply
    return "classify", rest


def match_start(text):
    """The goal after a start phrase ("" for the phrase alone), or None without one."""
    match = START.match(text)
    return match[1].strip() if match else None


def is_echo(text, last_tts, during_speech=False):
    """True when the words look like the glasses' last speech heard back.

    After that speech, it takes three or more unaddressed words: short replies such as "done" still
    count, and so does anything starting with "Aria", since the hints ("Say Aria, trial start.")
    invite those words. During it, two are enough with or without "Aria", so the hint "Say Aria,
    stop to cancel." can't close the trial if the microphone ever hears it.

    Args:
        text: what the recognizer heard.
        last_tts: the last line sent to the glasses, or None.
        during_speech: the words began before that line should have ended.
    """
    words = normalized(text)
    if during_speech:
        enough = len(words.split()) >= 2
    else:
        enough = not strip_wake(text)[0] and len(words.split()) >= 3
    return enough and f" {words} " in f" {normalized(last_tts or '')} "


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
        feedback. Only exact words after a leading "Aria" can close or replace a trial, apart from
        done or yes answering the system's question.
    """
    if not text.strip():
        return "discard", ""
    said_aria, rest = strip_wake(text)
    addressed = said_aria or window == "aria"
    if said_aria and not rest:
        return "listen", ""
    # A stale reply window must not reach the idle hint: nobody asked for this speech.
    if not addressed and window == "reply" and not task.active:
        return "discard", ""
    word = normalized(rest)
    if not addressed and window != "goal":
        soft = SOFT_WAKE.match(text.strip())
        if soft:
            return soft_command(soft[1].strip(), task)
        if window is None:
            # A false "next" costs one replan, and these never close a trial.
            if not task.active or word not in NEXT_WORDS | REPEAT_WORDS:
                return "discard", ""
            command, reply = parse_reply(rest, task)
            return ("discard", "") if command == "confirm" else (command, reply)
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
