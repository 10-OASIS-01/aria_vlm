"""Debug-only stand-in for the glasses: publishes /aria/rgb/image_raw at 5 Hz.

With the image_path parameter set, every frame is that still image; otherwise a moving test pattern.
"""

import array
import time

import numpy as np
import rclpy
from PIL import Image as PILImage
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image


class FakeCamera(Node):
    def __init__(self):
        super().__init__("fake_camera")
        path = self.declare_parameter("image_path", "").value
        self.still = np.asarray(PILImage.open(path).convert("RGB")) if path else None
        self.pub = self.create_publisher(Image, "/aria/rgb/image_raw", qos_profile_sensor_data)
        self.count = 0
        self.create_timer(0.2, self.publish)
        self.get_logger().info(f"Publishing {path or 'test pattern'} at 5 Hz")

    def pattern(self):
        h, w = 480, 640
        pixels = np.zeros((h, w, 3), dtype=np.uint8)
        x = (self.count * 16) % w
        pixels[:, x : x + 40] = (255, 128, 0)
        return pixels

    def publish(self):
        pixels = self.still if self.still is not None else self.pattern()
        h, w = pixels.shape[:2]
        # Wall-clock stamps: guidance_node and the backend both compare frame age to time.time().
        now = time.time_ns()
        msg = Image(height=h, width=w, encoding="rgb8", step=w * 3)
        msg.data = array.array("B", pixels.tobytes())  # Same packing as aria_ros2.messages.
        msg.header.stamp.sec, msg.header.stamp.nanosec = divmod(now, 10**9)
        # Not aria_node's aria_rgb_optical_frame: the console and trials mark these as test frames.
        msg.header.frame_id = "fake_camera"
        self.pub.publish(msg)
        self.count += 1


def main():
    rclpy.init()
    node = FakeCamera()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
