"""An example bot: `python -m lircbot.examplebot <server> <port> <your nick> <channel without #> [--tls]`.

Its owner (checked with a WHOIS that the nick is logged in to services) can make it join, kick, say and quit.
"""

import argparse
import logging

from lircbot import ircBot

HELP = [
    "I am an example bot.",
    "I have 4 functions, they are Join, Kick, Quit and Say.",
    'Join (joins a channel); Usage: "!join #<channel>"',
    'Kick (kicks a user); Usage: "!kick <nick> #<channel> <reason>"',
    'Quit (disconnects from the IRC server); Usage: "!quit [<quit message>]"',
    'Say (makes the bot say something); Usage: "!say <channel/user> <message>"',
    "The underlying framework is in no way limited to the above functions.",
    "This is merely an example of the framework's usage",
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("server")
    parser.add_argument("port", type=int)
    parser.add_argument("owner", help="your IRC nick; only you may command the bot")
    parser.add_argument("channel", help="channel to join, without the #")
    parser.add_argument("--tls", action="store_true", help="connect with TLS")
    parser.add_argument(
        "--send-interval", type=float, default=1.0, help="seconds between messages (anti-flood)"
    )
    args = parser.parse_args()
    channel = "#" + args.channel

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    bot = ircBot(
        args.server,
        args.port,
        "ExampleBot",
        "An example bot written with lircbot",
        tls=args.tls,
        send_interval=args.send_interval,
    )

    def refuse(recipient: str, name: str) -> None:
        bot.say(recipient, f"{name}: you could not be identified")

    def quit_bot(message: str) -> None:
        bot.stop(message or "Bye")

    def privmsg(sender: str, headers: list[str], message: str) -> None:
        if sender != args.owner:
            logging.info('PRIVMSG: "%s"', message)
            return
        reply_to = headers[0] if headers else sender
        command, _, rest = message.partition(" ")
        if command == "!say" and " " in rest:
            target, _, text = rest.partition(" ")
            bot.identify(sender, bot.say, (target, text), refuse, (reply_to, sender))
        elif command == "!quit":
            bot.identify(sender, quit_bot, (rest,), refuse, (reply_to, sender))
        elif command == "!join" and rest:
            bot.identify(sender, bot.join_chan, (rest,), refuse, (reply_to, sender))
        elif command == "!kick":
            nick, _, tail = rest.partition(" ")
            chan, _, reason = tail.partition(" ")
            if nick and chan and reason:
                bot.identify(sender, bot.kick, (nick, chan, reason), refuse, (reply_to, sender))

    def action(sender: str, headers: list[str], message: str) -> None:
        logging.info("%s %s %s", sender, headers, message)

    def end_of_motd(_sender: str, _headers: list[str], _message: str) -> None:
        bot.join_chan(channel)
        for line in HELP:
            bot.say(channel, line)

    bot.bind("PRIVMSG", privmsg)
    bot.bind("ACTION", action)
    bot.bind("376", end_of_motd)
    bot.debugging(True)
    bot.start()
    try:
        while input("type 'stop' to stop the bot\n") != "stop":
            pass
    except (EOFError, KeyboardInterrupt):
        pass
    bot.stop()
    bot.join(10)


if __name__ == "__main__":
    main()
