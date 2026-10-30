"""The open mic through the real node: transcripts are scripted, Whisper is not run."""

import time

import numpy as np
import pytest
from aria_msgs.msg import Audio
from conftest import trials, wait_for
from test_guidance import heard, kinds

from aria_guidance.intent import SAY_CANCEL, SAY_START


@pytest.fixture
def mic(node):
    """Turns the open mic on; say() hands the node what Whisper would have returned."""
    node.config["open_mic"] = True

    def say(text):
        node.audio_busy = True  # As hear_next does before the transcription.
        node.messages.put(("heard", time.monotonic(), {"text": text}))
        wait_for(lambda: not node.audio_busy)
        time.sleep(0.3)  # One more tick, for what the transcript started.

    return say


def started(probe, node, mic, goal="Aria, trial start, pick up the pen"):
    """Opens a trial by voice and waits for its first instruction."""
    mic(goal)
    return heard(probe, node, 1)


def test_chatter_does_nothing_and_leaves_no_trace(probe, node, camera, backend, mic, tmp_path):
    mic("Pick up the marker please")
    mic("Stop")
    assert node.task.state == "idle" and probe.tts == [] and trials(tmp_path) == []
    assert node.last_heard is None
    started(probe, node, mic)
    calls = len(backend.engine.calls)
    mic("In that area there is a mug")
    mic("Stop")
    assert node.task.active and backend.engine.intent_calls == []
    assert len(backend.engine.calls) == calls
    [(_, events, _)] = trials(tmp_path)
    assert not [e for e in events if e["kind"] == "transcript"]


def test_start_with_goal_opens_a_trial_without_the_model(
    probe, node, camera, backend, mic, tmp_path
):
    assert started(probe, node, mic) == "Pick up the pen."
    assert backend.engine.intent_calls == []
    [(trial, _, _)] = trials(tmp_path)
    assert trial["goal"] == "pick up the pen"


def test_start_alone_asks_and_the_next_words_are_the_goal(probe, node, camera, mic, tmp_path):
    mic("Aria, trial start")
    assert heard(probe, node, 1) == "What is the goal?"
    assert trials(tmp_path) == []
    node.speaking_until = 0.0
    mic("Stack the cups")  # No "Aria": the reply window is open.
    wait_for(lambda: node.task.goal == "Stack the cups")
    assert trials(tmp_path)[0][0]["goal"] == "Stack the cups"


def test_the_goal_window_closes(probe, node, camera, mic):
    mic("Aria, trial start")
    heard(probe, node, 1)
    node.window = ("goal", time.monotonic() - 0.1)
    mic("Stack the cups")
    assert node.task.state == "idle"


def test_following_the_idle_hint_works(probe, node, camera, mic):
    mic("Aria, what's next")
    assert heard(probe, node, 1) == SAY_START
    mic("Aria, trial start")  # The hint's own words, right after it.
    assert heard(probe, node, 2) == "What is the goal?"


def test_new_start_replaces_the_running_trial(probe, node, camera, mic, tmp_path):
    started(probe, node, mic)
    mic("Aria, trial start, stack the cups")
    heard(probe, node, 2)
    [(first, _, _), (second, _, _)] = trials(tmp_path)
    assert first["outcome"] == "replaced" and second["goal"] == "stack the cups"


@pytest.mark.parametrize(
    "said, outcome", [("Aria, stop.", "cancelled"), ("Aria, trial end", "completed")]
)
def test_exact_words_close_at_once(probe, node, camera, backend, mic, tmp_path, said, outcome):
    started(probe, node, mic)
    mic(said)
    assert trials(tmp_path)[0][0]["outcome"] == outcome
    assert backend.engine.intent_calls == []


def test_labels_become_feedback_with_the_raw_words(probe, node, camera, backend, mic, tmp_path):
    started(probe, node, mic)
    backend.engine.intent = "help"
    mic("Aria, I can't find it")
    assert heard(probe, node, 2) == "Okay."
    assert heard(probe, node, 3) == "Pick up the pen."
    assert backend.engine.calls[-1]["history"][-1]["text"] == "I can't find it"
    assert backend.engine.intent_calls[0]["text"] == "I can't find it"
    assert backend.engine.intent_calls[0]["goal"] == "pick up the pen"
    [(_, events, _)] = trials(tmp_path)
    [transcript] = [e for e in events if e["kind"] == "transcript"]
    assert transcript["text"] == "Aria, I can't find it" and transcript["intent"] == "help"
    [reply] = [e for e in events if e["kind"] == "reply"]
    assert reply["voice"] is True


def test_a_closing_label_only_hints(probe, node, camera, backend, mic, tmp_path):
    started(probe, node, mic)
    backend.engine.intent = "cancel"
    mic("Aria, forget it")
    assert heard(probe, node, 2) == SAY_CANCEL
    assert node.task.active and trials(tmp_path)[0][0]["outcome"] is None


def test_ignore_is_silent_and_a_failed_call_apologizes(probe, node, camera, backend, mic):
    started(probe, node, mic)
    backend.engine.intent = "ignore"
    mic("Aria, hold on")
    assert len(probe.tts) == 1
    backend.engine.intent = None
    mic("Aria, hm")
    assert heard(probe, node, 2) == "Sorry, I didn't catch that."
    backend.engine.intent = RuntimeError("boom")
    mic("Aria, hm")
    assert heard(probe, node, 3) == "Sorry, I didn't catch that."
    assert node.task.active


def test_note_goes_to_the_trial_without_a_plan(probe, node, camera, backend, mic, tmp_path):
    started(probe, node, mic)
    calls = len(backend.engine.calls)
    mic("Aria, note: the cup slipped")
    assert [e["text"] for e in trials(tmp_path)[0][1] if e["kind"] == "note"] == ["the cup slipped"]
    time.sleep(0.5)
    assert len(backend.engine.calls) == calls


def test_action_during_planning_drops_the_instruction(probe, node, camera, backend, mic, tmp_path):
    backend.engine.delay = 0.5
    mic("Aria, trial start, pick up the pen")
    wait_for(lambda: backend.engine.calls)
    backend.engine.intent = "correction"
    node.audio_busy = True  # The utterance is being transcribed when the plan comes back.
    wait_for(lambda: node.held_plan)
    assert probe.tts == []
    backend.engine.reply = "Pick up the blue pen."
    node.messages.put(("heard", time.monotonic(), {"text": "Aria, actually the blue one"}))
    assert heard(probe, node, 1) == "Okay."
    assert heard(probe, node, 2) == "Pick up the blue pen."
    assert backend.engine.calls[-1]["history"] == [
        {"role": "user", "text": "actually the blue one"}
    ]
    [(_, events, _)] = trials(tmp_path)
    assert kinds(events).count("plan_result") == 1


def test_chatter_during_planning_lets_the_instruction_through(probe, node, camera, backend, mic):
    backend.engine.delay = 0.5
    mic("Aria, trial start, pick up the pen")
    wait_for(lambda: backend.engine.calls)
    node.audio_busy = True
    wait_for(lambda: node.held_plan)
    node.messages.put(("heard", time.monotonic(), {"text": "where did I put that"}))
    assert heard(probe, node, 1) == "Pick up the pen."
    assert node.held_plan is None and len(backend.engine.calls) == 1


def test_stop_during_planning_drops_the_instruction(probe, node, camera, backend, mic, tmp_path):
    backend.engine.delay = 0.5
    mic("Aria, trial start, pick up the pen")
    wait_for(lambda: backend.engine.calls)
    node.audio_busy = True
    wait_for(lambda: node.held_plan)
    node.messages.put(("heard", time.monotonic(), {"text": "Aria, stop"}))
    wait_for(lambda: trials(tmp_path)[0][0]["outcome"] == "cancelled")
    time.sleep(0.4)
    assert probe.tts == [] and node.held_plan is None


def test_talk_is_refused_while_a_plan_is_held(probe, node, camera, backend, mic):
    backend.engine.delay = 0.3
    mic("Aria, trial start, pick up the pen")
    wait_for(lambda: backend.engine.calls)
    node.endpointer.speaking = True  # Someone is talking, so the plan stays held.
    wait_for(lambda: node.held_plan)
    accepted, _ = node.talk(True)
    assert not accepted and not node.audio_active


def test_speech_in_progress_holds_the_plan_then_gives_up(probe, node, camera, backend, mic):
    backend.engine.delay = 0.3
    mic("Aria, trial start, pick up the pen")
    wait_for(lambda: backend.engine.calls)
    node.endpointer.speaking = True  # Mid-sentence, so nothing has reached the queue yet.
    wait_for(lambda: node.held_plan)
    assert probe.tts == []
    node.held_since -= 8.0  # Ongoing chatter must not starve the instruction.
    assert heard(probe, node, 1) == "Pick up the pen."


def test_hold_to_talk_needs_no_wake_word(probe, node, camera, backend, mic, tmp_path):
    started(probe, node, mic)
    node.messages.put(("transcript", node.task.epoch, {"text": "I can't find it"}))
    heard(probe, node, 2)
    assert backend.engine.intent_calls == []  # Exact words and feedback, no classifier.
    assert backend.engine.calls[-1]["history"][-1]["text"] == "I can't find it"


def audio_block(probe_pub, level, stamp_ms, samples=1600):
    message = Audio()
    message.header.stamp.sec = int(stamp_ms // 1000)
    message.header.stamp.nanosec = int(stamp_ms % 1000 * 1e6)
    message.sample_rate, message.channels = 16000, 1
    message.data = np.full(samples, int(level * 2**31 / 16), dtype=np.int32).tolist()
    probe_pub.publish(message)


def test_microphone_to_trial_end_to_end(probe, node, executor, camera, tmp_path, monkeypatch):
    """Real Audio messages: endpointing, the utterance queue, the deaf window and saved sound."""
    node.config["open_mic"] = True
    heard_wavs = []

    def transcribe(wav):
        heard_wavs.append(wav)
        return {"text": "Aria, trial start, pick up the pen"}

    monkeypatch.setattr(node.client, "transcribe", transcribe)
    publisher = probe.create_publisher(Audio, "/aria/audio", 100)
    wait_for(lambda: publisher.get_subscription_count() > 0)
    now = time.time() * 1000

    def play(level, blocks):
        nonlocal now
        for _ in range(blocks):
            audio_block(publisher, level, now)
            now += 100
            time.sleep(0.01)

    play(0.001, 5)  # Room noise.
    assert heard_wavs == []
    play(0.02, 6)  # Speech: 0.6 s.
    play(0.001, 9)  # 0.9 s of trailing quiet ends it.
    wait_for(lambda: len(heard_wavs) == 1)
    assert heard(probe, node, 1) == "Pick up the pen."
    # The glasses are speaking: the same loudness must not become an utterance.
    node.speaking_until = time.monotonic() + 10
    play(0.02, 6)
    play(0.001, 9)
    time.sleep(0.3)
    assert len(heard_wavs) == 1
    assert not node.endpointer.speaking
    node.trial.close("completed")
    [(trial, _, _)] = trials(tmp_path)
    assert trial["audio"]["rate"] == 16000
    assert trial["audio"]["start_ms"] < trial["started_ms"]  # Opens with the buffered blocks.
    pcm = next(tmp_path.glob("backend/guidance/*/trials/*/audio.pcm"))
    assert pcm.stat().st_size >= 2 * 1600 * 30
