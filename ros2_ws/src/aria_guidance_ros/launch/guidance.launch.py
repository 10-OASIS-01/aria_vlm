from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    config = Path(get_package_share_directory("aria_guidance_ros")) / "config" / "guidance.yaml"
    return LaunchDescription(
        [
            DeclareLaunchArgument("backend_url", default_value="http://127.0.0.1:8785"),
            DeclareLaunchArgument("config", default_value=str(config)),
            Node(
                package="aria_guidance_ros",
                executable="guidance_node",
                name="guidance_node",
                output="screen",
                parameters=[
                    LaunchConfiguration("config"),
                    {
                        "backend_url": LaunchConfiguration("backend_url"),
                    },
                ],
            ),
            # The operator console: open ws://localhost:8765 in Foxglove, load operator_layout.json.
            Node(
                package="foxglove_bridge",
                executable="foxglove_bridge",
                name="foxglove_bridge",
                parameters=[
                    {
                        "port": 8765,
                        "topic_whitelist": ["/guidance/.*", "/aria/tts", "/rosout"],
                        "service_whitelist": ["/guidance/.*"],
                        "client_topic_whitelist": ["/guidance/.*"],
                    }
                ],
            ),
        ]
    )
