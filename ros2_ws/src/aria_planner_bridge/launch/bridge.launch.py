from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    config = Path(get_package_share_directory("aria_planner_bridge")) / "config" / "bridge.yaml"
    return LaunchDescription(
        [
            DeclareLaunchArgument("backend_url", default_value="http://127.0.0.1:8785"),
            DeclareLaunchArgument("config", default_value=str(config)),
            Node(
                package="aria_planner_bridge",
                executable="planner_bridge",
                name="planner_bridge",
                output="screen",
                parameters=[
                    LaunchConfiguration("config"),
                    {
                        "backend_url": LaunchConfiguration("backend_url"),
                    },
                ],
            ),
        ]
    )
