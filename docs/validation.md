# Validation record

Actual run results are published at `api/reports/latest` on the dashboard and included in the downloadable `validation.json`. The source handoff contains the runtime application, configuration, deployment guidance and replay client. Verification tools and generated fixtures are maintained separately from that handoff.

Hardware scope: the notebook deployment does not have a connected Aria Gen2 device or a ROS 2 Humble runtime. Pure Python buffering, image/audio conversion and backend state transitions can be checked here; ROS topic transport and onboard playback must be checked on the lab's Aria host using [the checklist](hardware-checklist.md).

Rendered trials use MuJoCo with a kinematic executor. They do not test contact physics, speech from the wearer, or actual human task completion. The current implementation uses raw RGB observations with the image processor's resizing, without Aria-specific fisheye rectification.
