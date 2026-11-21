import logging
import socket
import threading

import pytest

from lircbot import ircBot


def test_registers_with_its_nick_and_real_name(server, make_bot):
    make_bot()
    assert server.wait_for_command("NICK", "TestBot")
    assert server.wait_for(lambda line: line.startswith("USER ") and line.endswith(":a test bot"))


def test_privmsg_reaches_the_bound_callback(server, make_bot, inbox):
    bot = make_bot()
    bot.bind("PRIVMSG", inbox)
    server.send(":alice!al@host.example PRIVMSG TestBot :héllo ✓: with colons")
    assert inbox.next() == ("alice", ["TestBot"], "héllo ✓: with colons")


def test_channel_message_headers_hold_the_channel(server, make_bot, inbox):
    bot = make_bot()
    bot.bind("PRIVMSG", inbox)
    server.send(":alice!al@host PRIVMSG #chan :hi all")
    assert inbox.next() == ("alice", ["#chan"], "hi all")


def test_ctcp_action_becomes_an_action_not_a_privmsg(server, make_bot, inbox):
    bot = make_bot()
    privmsg, action = type(inbox)(), inbox
    bot.bind("PRIVMSG", privmsg)
    bot.bind("ACTION", action)
    server.send(":alice!al@host PRIVMSG #chan :\x01ACTION waves\x01")
    assert action.next() == ("alice", ["#chan"], "waves")
    assert privmsg.next(timeout=0.3) is None


def test_numerics_can_be_bound(server, make_bot, inbox):
    bot = make_bot()
    bot.bind("372", inbox)
    server.send(":irc.test 372 TestBot :- message of the day")
    assert inbox.next() == ("irc.test", ["TestBot"], "- message of the day")


def test_binding_a_type_again_replaces_the_earlier_callback(server, make_bot, inbox):
    bot = make_bot()
    first = type(inbox)()
    bot.bind("PRIVMSG", first)
    bot.bind("PRIVMSG", inbox)
    server.send(":alice!al@host PRIVMSG #c :hi")
    assert inbox.next() is not None
    assert first.next(timeout=0.3) is None


def test_a_failing_callback_does_not_stop_the_bot(server, make_bot, inbox, caplog):
    bot = make_bot()

    def boom(*_args):
        raise RuntimeError("callback bug")

    bot.bind("PRIVMSG", boom)
    bot.bind("NOTICE", inbox)
    with caplog.at_level(logging.ERROR):
        server.send(":alice!al@host PRIVMSG #c :one")
        server.send(":alice!al@host NOTICE #c :two")
        assert inbox.next() == ("alice", ["#c"], "two")
    assert "error in a callback" in caplog.text


def test_the_bot_answers_server_pings(server, make_bot):
    make_bot()
    server.send("PING :token123")
    assert server.wait_for(lambda line: line.startswith("PONG") and "token123" in line)


def test_say_sends_utf8_in_order(server, make_bot):
    bot = make_bot()
    bot.say("alice", "one ✓")
    bot.say("#chan", "two")
    bot.say("#chan", "three")
    assert server.wait_for_command("PRIVMSG", "#chan", "three")
    assert server.commands("PRIVMSG") == [
        ("PRIVMSG", ("alice", "one ✓")),
        ("PRIVMSG", ("#chan", "two")),
        ("PRIVMSG", ("#chan", "three")),
    ]


def test_a_callback_can_reply(server, make_bot):
    bot = make_bot()
    bot.bind("PRIVMSG", lambda sender, _headers, message: bot.say(sender, "you said: " + message))
    server.send(":alice!al@host PRIVMSG TestBot :ping")
    assert server.wait_for_command("PRIVMSG", "alice", "you said: ping")


def test_channel_commands_are_formatted_for_irc(server, make_bot):
    bot = make_bot()
    bot.join_chan("#c")
    bot.kick("bob", "#c", "bye now")
    bot.ban("*!*@bad.example", "mallory", "#c", "spam")
    bot.unban("*!*@bad.example", "#c")
    bot.send("TOPIC #c :a new topic")
    assert server.wait_for_command("TOPIC", "#c", "a new topic")
    assert server.commands("JOIN", "KICK", "MODE", "TOPIC") == [
        ("JOIN", ("#c",)),
        ("KICK", ("#c", "bob", "bye now")),
        ("MODE", ("#c", "+b", "*!*@bad.example")),
        ("KICK", ("#c", "mallory", "spam")),
        ("MODE", ("#c", "-b", "*!*@bad.example")),
        ("TOPIC", ("#c", "a new topic")),
    ]


def test_identify_calls_the_approved_callback_for_a_registered_nick(server, make_bot, inbox):
    bot = make_bot()
    denied = type(inbox)()
    bot.identify("alice", inbox, ("yes", 1), denied, ("no",))
    assert inbox.next() == ("yes", 1)
    assert denied.next(timeout=0.3) is None


def test_identify_calls_the_denied_callback_for_an_unregistered_nick(server, make_bot, inbox):
    bot = make_bot()
    approved = type(inbox)()
    bot.identify("mallory", approved, ("yes",), inbox, ("no", "mallory"))
    assert inbox.next() == ("no", "mallory")
    assert approved.next(timeout=0.3) is None


def test_identify_checks_several_nicks_one_after_another(server, make_bot, inbox):
    bot = make_bot()
    bot.identify("alice", inbox, ("alice ok",), inbox, ("alice denied",))
    bot.identify("mallory", inbox, ("mallory ok",), inbox, ("mallory denied",))
    assert inbox.next() == ("alice ok",)
    assert inbox.next() == ("mallory denied",)


@pytest.mark.parametrize("reset", [False, True], ids=["closed", "reset"])
def test_the_bot_connects_again_after_the_server_drops_it(server, make_bot, inbox, reset):
    bot = make_bot()
    bot.bind("PRIVMSG", inbox)
    server.drop(reset=reset)
    assert server.wait_for(lambda _line: server.count(lambda x: x.startswith("USER ")) == 2, 30), (
        "the bot did not register a second time"
    )
    assert server.connections == 2
    server.send(":alice!al@host PRIVMSG TestBot :back again")
    assert inbox.next() == ("alice", ["TestBot"], "back again")


def test_stop_quits_and_ends_the_thread(server, make_bot):
    bot = make_bot()
    bot.stop()
    bot.join(15)
    assert not bot.is_alive()
    assert bot.stopped()
    assert server.wait_for(lambda line: line.startswith("QUIT"))


def test_the_bot_gives_up_after_its_retries():
    with socket.socket() as probe:  # a port nothing listens on: connection refused
        probe.bind(("127.0.0.1", 0))
        closed_port = probe.getsockname()[1]
    bot = ircBot("127.0.0.1", closed_port, "TestBot", "no server there")
    bot.retries(2)
    bot._retry_delay = 0.01
    bot.start()
    bot.join(15)
    assert not bot.is_alive()


def test_sending_before_start_is_an_error():
    bot = ircBot("127.0.0.1", 6667, "TestBot", "x")
    with pytest.raises(RuntimeError, match="start"):
        bot.say("#c", "hi")


def test_timeout_threshold_must_be_positive():
    bot = ircBot("127.0.0.1", 6667, "TestBot", "x")
    bot.timeout_threshold(30)
    with pytest.raises(ValueError, match="positive"):
        bot.timeout_threshold(0)


def test_methods_are_safe_to_call_from_other_threads(server, make_bot):
    bot = make_bot()
    threads = [threading.Thread(target=bot.say, args=("#c", f"msg {i}")) for i in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert server.wait_for(lambda _line: len(server.commands("PRIVMSG")) == 10)
