"""Relay between guidance_node and the web console's Live section.

The node pushes its state and the preview JPEG and long-polls for operator commands; the browser
long-polls the state and posts commands. Nothing here is task state: the node owns the task, and
a backend restart only drops the relay until the node's next push.
"""

import asyncio
import time

NODE_TIMEOUT_S = 5.0  # The node pushes state at least every 2 s while it runs.


class LiveRelay:
    def __init__(self):
        self.state, self.version = {}, 0
        self.preview = None
        self.commands = []
        self.node_seen = 0.0
        self.changed = asyncio.Condition()

    def node_online(self):
        return time.time() - self.node_seen < NODE_TIMEOUT_S

    def snapshot(self):
        return {**self.state, "version": self.version, "node_online": self.node_online()}

    async def wait(self, predicate, timeout):
        async with self.changed:
            try:
                await asyncio.wait_for(self.changed.wait_for(predicate), timeout)
            except asyncio.TimeoutError:
                pass

    async def notify(self):
        async with self.changed:
            self.changed.notify_all()

    async def push_state(self, state):
        self.node_seen = time.time()
        if state != self.state:
            self.state, self.version = state, self.version + 1
            await self.notify()

    async def push_preview(self, jpeg):
        self.node_seen = time.time()
        self.preview = jpeg

    async def wait_state(self, after, timeout):
        """The state once its version passes `after`, or as it stands after `timeout`."""
        await self.wait(lambda: self.version > after, timeout)
        return self.snapshot()

    async def add_command(self, command):
        self.commands.append(command)
        await self.notify()

    async def take_commands(self, timeout):
        """Pending commands, oldest first, waiting up to `timeout` for the first one."""
        self.node_seen = time.time()
        await self.wait(lambda: bool(self.commands), timeout)
        commands, self.commands = self.commands, []
        self.node_seen = time.time()
        return commands
