---
target: console workflow (Live + System row + Trials)
total_score: 24
max_score: 40
na_heuristics: 
p0_count: 0
p1_count: 4
target_identity: "file:/home/sparklab/EgoBranch/aria_vlm/src/aria_guidance/web/index.html"
target_fingerprint: "sha256:968f7b7430a6ed5dcde167e38429d67b5737958d739cb17625dfae872e743cae"
target_path: /home/sparklab/EgoBranch/aria_vlm/src/aria_guidance/web/index.html
timestamp: 2026-10-29T19-58-38Z
slug: src-aria-guidance-web-index-html
---
Method: dual-agent (A: design review sub-agent, B: detector and browser sub-agent). Focus: operator workflow, start console to reviewed trial, first-time lab member.

## Score 24/40 (Acceptable)
1 Status 3 (Observing no progress; clarification shown as Act) · 2 Real world 2 (guidance_node, Node offline, /guidance/goal, exit codes; who says done) · 3 Control 3 (failed box not dismissible) · 4 Consistency 2 (Done changes meaning by state; Connect contradicts Start) · 5 Prevention 3 (Talk with no goal becomes goal) · 6 Recognition 2 (voice vocabulary only in README) · 7 Efficiency 3 · 8 Minimalism 3 (traceback loudest) · 9 Recovery 2 (stack traces; camera Off -> Observing forever) · 10 Help 1 (stale Connect, no inline help).

## Priority issues
- [P1] Space on a focused Live control (Start, camera radios) starts push-to-talk (ownsKeys only excludes buttons outside #live). Fix: Space/D/N/R only when focus is not on a control; arrow keys in the radiogroup. -> harden
- [P1] Done/Next meaning changes by state invisibly. Fix: state labels (Step done / Re-plan; Confirm complete / Not yet), band names who acts, primary button follows the band. -> clarify
- [P1] clarification status shown as "Act, then say done". Fix: STATE_BANDS.clarification; mark instruction "Last instruction" in error. -> clarify
- [P1] Failed process = raw permanent traceback. Fix: one-line cause, Show log disclosure, red dot on the segment, clear on other camera/dismiss, wrap. -> harden
- [P2] Observing: no reason/progress; camera Off not caught; idle text jargon. -> onboard
- [P2] No inline voice/Talk/camera help; Connect stale (ros2 launch); outcome labels unexplained. -> onboard
- [P2] a11y: segmented focus ring clipped by overflow hidden; 390 top nav 31px; sidebar/footer contrast 2.1-4.1 and 9-10px. -> polish

## Knowledge not in the UI (~14)
node vs model; camera choices and pairing; 10 s window; goal can be typed early; Done vs Next per state; who says done; Talk = glasses mic; voice vocabulary; whole-recording rule; why Talk refused; speech guard; outcome labels; notes save on blur; record location.

## Tutorial recommendation
Inline hints and state-aware labels first. Optional point-only tour (never presses), skippable, replayable from "?": Start guidance_node; choose camera; band + 10 s window; Done/Next/Talk; trial link to Trials.

## Detector
CLI 3 false positives. Overlay: Live 9 (sidebar/footer), Trials 9, Connect 34 (old cards 2.4-3.1:1, 9-11px). System row, band, goal and camera areas all >=4.9:1, controls 44px.
