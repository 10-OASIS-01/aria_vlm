Merged Interface Proposal Hitesh was thinking

Summary
We propose integrating aria2_ros2 and aria_vlm into a unified system featuring a single task manager and operator dashboard, deployed on our local GPU lab workstation:
Maintain aria2_ros2 in its current state.
Refactor the aria_vlm bridge into a dedicated guidance_node to manage task execution.
Convert the GPU backend into a stateless model server.
Build a unified web interface displaying the live video feed alongside model-generated instructions.

Current Setup & Identified Issues
Currently, aria2_ros2 executes aria_node, publishing /aria/rgb/image_raw, /aria/gaze, and /aria/audio, while processing TTS requests on /aria/tts and rendering through Rerun.
The aria_vlm component introduces aria_planner_bridge and a FastAPI service backed by Qwen3.5-9B. The bridge transmits 20 video frames over a 10-second window to the backend, outputs generated responses to /aria/tts, and listens on /planner/goal, /planner/command, and /planner/listen.

Key Challenges:
Fragmented workflow: Operators must monitor Rerun for visual streams, user terminals for interaction, and /planner/status or the web workbench for outputs.
Distributed state management: Task state is split between the bridge and backend, requiring complex handling for HTTP 409 and 429 errors.
Coupled backend: Stateful model hosting makes swapping architectures or replaying recorded sessions difficult.
Unused gaze data: Gaze telemetry is published but ignored by the planning module.
Missing session logs: Model requests and responses are not persistently archived within the Aria session directory.

Proposed Architecture
A centralized guidance_node will act as the single source of truth for task execution. Running locally on the lab workstation, it communicates with the inference server via local HTTP calls without requiring SSH tunnels. Both aria_node and guidance_node will log directly to the session directory.
aria_node (unmodified aria2_ros2): Handles device connections, streams image, gaze, and audio data, and executes TTS requests.
guidance_node (evolved from aria_planner_bridge): Tracks the active goal, execution history, and a 10-second rolling window of frames and gaze data; orchestrates model invocation and speech synthesis.
Inference Server (refactored aria_vlm backend): Serves Qwen3.5-9B and faster-whisper statelessly across requests.
foxglove_bridge: Exposes ROS 2 topics to the web console via WebSocket.
Web Console: Displays visual feed with gaze overlay, active prompts, transcripts, and task control actions (Start, Done, Repeat, Cancel, Talk).



System Workflow
The operator provides a task goal via text or voice input (processed through the ASR service).
The guidance_node buffers approximately 10 seconds of video and gaze inputs.
The node packages 20 frames, gaze tracking, active goal, and history, sending them to the model backend.
The model generates the next instruction step.
The guidance_node forwards the instruction to /aria/tts and updates the web console interface.
The user performs the action and responds ("done", "repeat", correction, or cancel), triggering the next iteration until task completion.

System Changes Overview

Component
Current Design
Proposed Design
aria2_ros2
Driver and Rerun visualizer
Unchanged (pinned at 1af73bd - Needs to be updated by the latest commit)
Bridge
aria_planner_bridge using /planner/* topics
guidance_node owning task state and injecting gaze telemetry
Controls
/planner/goal, /planner/command, /planner/status
/guidance/task action for lifecycle management; /guidance/reply for user feedback
Backend
FastAPI handling state and hosting the workbench
Stateless endpoints (/v1/plan, /v1/asr) maintaining /api/health checks
Operator UI
Split across Rerun, shell terminals, and workbench
Unified web dashboard backed by foxglove_bridge
Logging
Session VRS video and clock.csv records
Appends guidance.jsonl and logs frames submitted to model


Note: /guidance and /v1 namespaces are proposed. foxglove_bridge refers to the standard ROS package (ros-humble-foxglove-bridge) for streaming data over WebSockets.

Rollout Plan

Phase
Implementation Tasks
Acceptance Criteria
1. Single Rerun View
Integrate state logging, instruction outputs, and frame overlays into aria_node's Rerun instance; generate guidance.jsonl.
Operators can monitor complete execution runs within a single Rerun window.
2. Centralized Task State
Implement /guidance/task actions and /guidance/reply interface; decouple state from backend; add gaze integration.
Backend service can restart mid-task without corrupting session progression.
3. Web Console
Deploy foxglove_bridge and launch the unified web dashboard.
Trials execute end-to-end without needing direct terminal interaction.


Open Questions
Should we adopt the pre-built Foxglove application UI, or develop a tailored frontend using @foxglove/ws-protocol? - pre built is fine for now i think
Is workstation GPU VRAM sufficient to load Qwen3.5-9B alongside a 20-frame input buffer while running ROS nodes and visualizers concurrently? (Official aria_vlm guidelines recommend 32 GB VRAM). - need to test on the workstation
Which gaze input gives the best instructions: a gaze marker on each frame, or the marker plus one or two high-resolution crops around the gaze point? Frames are already downscaled to about 131,072 pixels each, so crops add detail without losing scene context. Test both on recorded sessions. Either needs /aria/gaze projected into image pixels using the camera calibration.
Should the existing web workbench be retained for offline evaluation and experimentation? - Yes and we can add anything new there 








