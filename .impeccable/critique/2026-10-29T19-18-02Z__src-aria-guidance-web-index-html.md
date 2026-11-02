---
target: workbench console (Live and Trials)
total_score: 23
max_score: 40
na_heuristics: 
p0_count: 1
p1_count: 3
target_identity: "file:/home/sparklab/EgoBranch/aria_vlm/src/aria_guidance/web/index.html"
target_fingerprint: "sha256:0deafe81ef70756a9f87d120f7381c7d05d0bc9cecabad3cc568d236a40bcb90"
target_path: /home/sparklab/EgoBranch/aria_vlm/src/aria_guidance/web/index.html
timestamp: 2026-10-29T19-18-02Z
slug: src-aria-guidance-web-index-html
---
Method: dual-agent (A: design review sub-agent, B: detector and browser sub-agent)

## Design health score: 23/40 (Acceptable)
1 Visibility 2 (goal and camera source not shown; band label/detail contradict) · 2 Real world 3 (jargon: guidance_node, session stamps) · 3 Control 2 (Cancel/Set goal unguarded) · 4 Consistency 3 ("Live" badge vs Live console; Overview metrics stale) · 5 Error prevention 1 (shortcuts fire when any pixel of #live is visible) · 6 Recognition 2 (goal must be recalled; placeholder looks like a goal) · 7 Efficiency 3 · 8 Minimalism 3 (controls mix three moments; marketing page container) · 9 Recovery 2 (speech-guard-swallowed reply looks applied) · 10 Help 2.

## Priority issues
- [P0] Camera source/health invisible: a still photo passes for live vision. Fix: camera strip (Aria glasses / TEST CAMERA, Hz, frame age, amber >1 s, red >3 s, dim image); mark fake-camera trials. Node already reports camera {source,hz,age_s} and trial.json camera. -> harden
- [P1] Shortcuts leak into Trials (Space on the video opens the mic, N sends Next); Cancel and Set goal unguarded. Fix: shortcuts only when Live is the current view; ignore video/#trials targets; Cancel as separated press-again confirm; "Replace goal" with confirm. -> harden
- [P1] Active goal not shown; placeholder equals a real goal. Fix: "Goal · ..." above instruction; neutral placeholder; New goal disclosure while a task runs. -> clarify
- [P1] Band label/detail contradict (speech_guard unmapped; raw codes fall through). Fix: map node sub-state first; label = one action, detail = reason. -> clarify
- [P2] Console buried in a marketing page; phone puts controls ~900px down. Fix: route-switched views, one-screen console at >=1200px, controls first + sticky Done/Next on phone. -> layout
- [P2] Contrast/size: muted text 3.56-4.05:1 at 11-13px, badges 3.28-4.43 at 10px, kbd hints 3.04-3.64 (opacity .6); secondary controls 42-43px, trial link 18px, timeline rows 38px. Fix: --muted ~#5f6b66, badges >=11px darker, kbd no opacity, 44px targets. -> polish

## Persona red flags
- Operator at 2 m: only band label and instruction readable; goal/camera missing; Hold to talk styled more dangerous than Cancel.
- First-timer: marketing hero first, ROS jargon, unclear whether Done is pressed or spoken, fake camera undetectable.
- Power user: flat trial list, no unreviewed filter or j/k, shortcut leakage, no quick verdict in Live.

## Minor
trial.json steps only written at close ("0 steps" while open); "Live" badge -> "Running"; timeline hides Thinking spans and ignored replies; 4:3 preview letterboxes square Aria RGB; Node connected badge far from band.

## Detector
CLI: 4 warnings, all false positives (stylesheet unread, hidden imgs, placeholder copy). Overlay: 94 page-wide findings; in-scope confirmed low contrast, tiny text, small targets. Focus visible, Tab order logical, no horizontal overflow at 390.
