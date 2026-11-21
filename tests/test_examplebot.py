"""The example bot, run as a real process against the fake server."""

import subprocess
import sys


def test_example_bot_joins_helps_and_obeys_its_identified_owner(server):
    process = subprocess.Popen(  # noqa: S603 - our own interpreter and module
        [
            sys.executable,
            "-m",
            "lircbot.examplebot",
            "127.0.0.1",
            str(server.port),
            "alice",
            "lobby",
            "--send-interval",
            "0",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        assert server.wait_for_command("JOIN", "#lobby"), (
            "the bot did not join its channel at the end of the MOTD"
        )
        assert server.wait_for_command("PRIVMSG", "#lobby", "I am an example bot.")

        # The owner (alice, registered on the fake server) can command it ...
        server.send(":alice!al@host PRIVMSG ExampleBot :!say #lobby hello there")
        assert server.wait_for_command("PRIVMSG", "#lobby", "hello there")
        server.send(":alice!al@host PRIVMSG ExampleBot :!join #second")
        assert server.wait_for_command("JOIN", "#second")

        # ... and nobody else can, even with the same command.
        server.send(":mallory!m@host PRIVMSG ExampleBot :!say #lobby not allowed")
        server.send(":alice!al@host PRIVMSG ExampleBot :!say #lobby after mallory")
        assert server.wait_for_command("PRIVMSG", "#lobby", "after mallory")
        assert ("PRIVMSG", ("#lobby", "not allowed")) not in server.commands("PRIVMSG")

        server.send(":alice!al@host PRIVMSG ExampleBot :!quit goodbye")
        assert server.wait_for_command("QUIT", "goodbye"), "the owner's !quit did not quit with the message"
    finally:
        process.communicate("stop\n", timeout=20)
    assert process.returncode == 0
