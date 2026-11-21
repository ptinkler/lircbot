"""How a parsed IRC message maps onto the bound callbacks (no network)."""

from unittest.mock import MagicMock

import pytest
from pydle.features.rfc1459.parsing import RFC1459Message

from lircbot import ircBot


def dispatch(bot, raw):
    bot._dispatch(RFC1459Message.parse(raw.encode("utf-8") + b"\r\n"))


@pytest.fixture
def bot():
    return ircBot("irc.example", 6667, "TestBot", "test")


@pytest.mark.parametrize(
    ("raw", "msgtype", "expected"),
    [
        (":alice!al@host PRIVMSG #c :hello there", "PRIVMSG", ("alice", ["#c"], "hello there")),
        (":alice!al@host PRIVMSG #c :\x01ACTION waves\x01", "ACTION", ("alice", ["#c"], "waves")),
        (
            ":irc.example 376 TestBot :End of /MOTD command.",
            "376",
            ("irc.example", ["TestBot"], "End of /MOTD command."),
        ),
        (":alice!al@host JOIN :#c", "JOIN", ("alice", [], "#c")),
        (":alice!al@host KICK #c bob :bye", "KICK", ("alice", ["#c", "bob"], "bye")),
        ("PING :token", "PING", ("", [], "token")),
    ],
)
def test_message_types_and_arguments(bot, raw, msgtype, expected):
    callback = MagicMock()
    bot.bind(msgtype, callback)
    dispatch(bot, raw)
    callback.assert_called_once_with(*expected)


def test_a_privmsg_that_is_an_action_does_not_reach_the_privmsg_binding(bot):
    callback = MagicMock()
    bot.bind("PRIVMSG", callback)
    dispatch(bot, ":alice!al@host PRIVMSG #c :\x01ACTION waves\x01")
    callback.assert_not_called()


def test_unbound_messages_are_ignored(bot):
    dispatch(bot, ":alice!al@host PRIVMSG #c :hi")
