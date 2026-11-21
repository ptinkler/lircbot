import queue

import pytest
from fake_irc import FakeIrcServer

from lircbot import ircBot


@pytest.fixture
def server():
    server = FakeIrcServer(registered_nicks={"alice"})
    yield server
    server.close()


@pytest.fixture
def make_bot(server):
    """Start a bot against the fake server (no pause between messages); stop them all afterwards."""
    bots = []

    def make(**kwargs):
        bot = ircBot("127.0.0.1", server.port, "TestBot", "a test bot", send_interval=0, **kwargs)
        bots.append(bot)
        bot.start()
        assert bot.wait_until_ready(15), "the bot never connected"
        assert server.wait_for(lambda line: line.startswith("USER ")), "the bot never registered"
        # A PING/PONG round trip means the bot has processed the whole registration.
        server.send("PING :sync")
        assert server.wait_for(lambda line: line.startswith("PONG") and "sync" in line), "no PONG"
        return bot

    yield make
    for bot in bots:
        bot.stop()
        bot.join(15)


@pytest.fixture
def inbox():
    """A callback that records what it is called with, and a way to wait for the next call."""

    class Inbox:
        def __init__(self):
            self.calls = queue.Queue()

        def __call__(self, *args):
            self.calls.put(args)

        def next(self, timeout=15):
            try:
                return self.calls.get(timeout=timeout)
            except queue.Empty:
                return None

    return Inbox()
