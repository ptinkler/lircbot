# lircbot

A lightweight IRC bot framework: one thread, one connection, plain callbacks. It runs on [pydle](https://github.com/shizmob/pydle) (asyncio), so TLS, SASL login, IRCv3 and PING/PONG are handled for you.

```python
from lircbot import ircBot

bot = ircBot("irc.libera.chat", 6697, "MyBot", "A little bot", tls=True)


def on_message(sender, headers, message):
    if message == "!ping":
        bot.say(headers[0], f"{sender}: pong")


bot.bind("PRIVMSG", on_message)
bot.bind("376", lambda *_: bot.join_chan("#mychannel"))  # end of the MOTD: now we can join
bot.start()  # runs in its own thread
...
bot.stop()
bot.join()
```

## API

| | |
|---|---|
| `ircBot(network, port, name, description, *, tls=False, tls_verify=True, password=None, sasl_username=None, sasl_password=None, send_interval=1.0)` | `name` is the nick, `description` the real name. `send_interval` is the pause between outgoing messages (anti-flood). |
| `bind(msgtype, callback)` | `callback(sender, headers, message)` runs for every IRC message of that type: a command (`PRIVMSG`, `JOIN`, `KICK` ...), a numeric (`"376"`) or `ACTION` for `/me`. `headers` are the parameters before the last, `message` is the last one. Binding a type again replaces the callback. |
| `say(recipient, message)`, `send(raw_line)`, `join_chan(channel)`, `kick(nick, channel, reason)`, `ban(mask, nick, channel, reason)`, `unban(mask, channel)` | Send to the server. Safe from any thread, including inside a callback. |
| `identify(nick, ok_func, ok_args, denied_func, denied_args)` | WHOIS `nick`; call `ok_func(*ok_args)` if they are logged in to services, else `denied_func(*denied_args)`. |
| `start()`, `stop(message)`, `join()`, `wait_until_ready(timeout)`, `reconnect()` | Thread control. The bot reconnects by itself after a drop; `retries(n)` and `timeout_threshold(seconds)` tune that. |
| `debugging(True)` | Log the protocol traffic at DEBUG level. |

Run the example: `uv run python -m lircbot.examplebot irc.example.org 6667 YourNick channel [--tls]`. It joins the channel and obeys `!say`, `!join`, `!kick` and `!quit` from you, after checking with a WHOIS that your nick is logged in.

## Development

```bash
uv sync                  # Python 3.10+ and the dev tools
uv run pytest            # the tests run the bot against a small fake IRC server on localhost
uv run ruff check . && uv run ruff format --check .
```

## History

Originally (2013) a hand-written Python 2 socket framework based on [Python-IRC-Bot-Framework](https://github.com/LukeusMaximus/Python-IRC-Bot-Framework). Version 2 keeps the API but hands the protocol to pydle; it needs Python 3.10 or newer.
