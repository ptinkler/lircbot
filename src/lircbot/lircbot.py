"""A small threaded IRC bot framework.

The original lircbot API (``ircBot``: bind, say, identify, kick, ...) on top of pydle, which does the
IRC protocol: TLS, SASL, IRCv3 capabilities, PING/PONG and nick handling. The bot runs its own asyncio
event loop in a thread, so it is started and stopped like a ``threading.Thread`` and every method is
safe to call from any thread, including from inside a callback.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Awaitable, Callable
from typing import Any

import pydle

log = logging.getLogger(__name__)

Callback = Callable[[str, list[str], str], Any]
Action = Callable[[pydle.Client], Awaitable[Any]]

ACTION_PREFIX = "\x01ACTION "
CTCP_END = "\x01"


class _Client(pydle.Client):
    """A pydle client that also hands every raw message to the bot's bindings."""

    RECONNECT_ON_ERROR = False  # the bot reconnects itself, with its own retry limit

    def __init__(self, bot: ircBot, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._bot = bot

    async def on_raw(self, message: Any) -> None:
        # Bindings first: pydle handles messages concurrently, and its own handling of one message can
        # take a while (it may look the sender up), which would reorder the callbacks.
        self._bot._dispatch(message)
        await super().on_raw(message)

    async def handle_forever(self) -> None:
        # pydle only copes with a read timeout here: a reset connection (an abrupt drop) would end its
        # reader task silently and leave the bot believing it is still connected.
        try:
            await super().handle_forever()
        except Exception as exc:
            log.warning("lost the connection: %s", exc)
            if self.connected:
                await self.disconnect(expected=False)

    async def on_disconnect(self, expected: bool) -> None:
        await super().on_disconnect(expected)
        self._bot._client_disconnected()


class ircBot(threading.Thread):  # noqa: N801 - the public name since 2013
    """An IRC bot that runs in its own thread.

    ``name`` is the nick; ``description`` is the real name. ``tls`` turns on TLS (certificates are
    verified unless ``tls_verify=False``). ``sasl_username`` and ``sasl_password`` log in with SASL
    PLAIN before registering; ``password`` is the server password. ``send_interval`` is the pause in
    seconds between outgoing messages, so a burst cannot get the bot flooded off the server.
    """

    def __init__(  # noqa: PLR0913 - one connection, one constructor
        self,
        network: str,
        port: int,
        name: str,
        description: str,
        *,
        tls: bool = False,
        tls_verify: bool = True,
        password: str | None = None,
        sasl_username: str | None = None,
        sasl_password: str | None = None,
        send_interval: float = 1.0,
    ) -> None:
        super().__init__(daemon=True)
        self.name = name
        self.desc = description
        self.network = network
        self.port = port
        self.connected = False
        self._tls = tls
        self._tls_verify = tls_verify
        self._password = password
        self._sasl = {"sasl_username": sasl_username, "sasl_password": sasl_password}
        self._send_interval = send_interval
        self._binds: dict[str, Callback] = {}
        self._retries = 5
        self._retry_delay = 2.0
        self._to_threshold = 3 * 60.0
        self._quit_message = "Stopping"
        self._stopped = threading.Event()
        self._ready = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._outgoing: asyncio.Queue[Action] | None = None
        self._wake: asyncio.Event | None = None
        self._client: _Client | None = None

    # ---- configuration -------------------------------------------------------------------------

    def bind(self, msgtype: str, callback: Callback) -> None:
        """Call ``callback(sender, headers, message)`` for every IRC message of this type.

        ``msgtype`` is the IRC command (``PRIVMSG``, ``JOIN``, ``KICK`` ...) or a numeric (``376``), plus
        ``ACTION`` for a ``/me``. ``headers`` are the parameters before the last one (for a ``PRIVMSG``
        that is the target) and ``message`` is the last parameter. Binding a type again replaces the
        earlier callback.
        """
        self._binds[msgtype] = callback

    def debugging(self, state: bool) -> None:
        """Log the protocol traffic (at DEBUG level) while on."""
        logging.getLogger("pydle").setLevel(logging.DEBUG if state else logging.NOTSET)
        log.setLevel(logging.DEBUG if state else logging.NOTSET)

    def retries(self, n: int) -> None:
        """How many connection attempts in a row before the bot gives up and stops."""
        self._retries = int(n)

    def timeout_threshold(self, t: float) -> None:
        """Seconds to wait for a connection attempt before it counts as failed."""
        t = float(t)
        if t <= 0:
            msg = "Timeout threshold must be positive."
            raise ValueError(msg)
        self._to_threshold = t

    # ---- sending (any thread) ------------------------------------------------------------------

    def say(self, recipient: str, message: str) -> None:
        """Send a message to a channel or a user."""
        self._send(lambda client: client.message(recipient, message))

    def send(self, string: str) -> None:
        """Send a raw IRC line (the line ending is added)."""
        self._send(lambda client: client.raw(string + "\r\n"))

    def join_chan(self, channel: str) -> None:
        self._send(lambda client: client.join(channel))

    def kick(self, nick: str, channel: str, reason: str) -> None:
        self._send(lambda client: client.rawmsg("KICK", channel, nick, reason))

    def ban(self, ban_mask: str, nick: str, channel: str, reason: str) -> None:
        """Ban the mask, then kick the nick."""
        self._send(lambda client: client.rawmsg("MODE", channel, "+b", ban_mask))
        self.kick(nick, channel, reason)

    def unban(self, ban_mask: str, channel: str) -> None:
        self._send(lambda client: client.rawmsg("MODE", channel, "-b", ban_mask))

    def identify(
        self,
        nick: str,
        approved_func: Callable[..., Any],
        approved_params: tuple[Any, ...],
        denied_func: Callable[..., Any],
        denied_params: tuple[Any, ...],
    ) -> None:
        """Check with a WHOIS that ``nick`` is logged in to services, then call one of the callbacks."""

        async def check(client: pydle.Client) -> None:
            info = await client.whois(nick)
            if info and (info.get("identified") or info.get("account")):
                self._call(approved_func, *approved_params)
            else:
                self._call(denied_func, *denied_params)

        self._send(check)

    def reconnect(self) -> None:
        """Drop the connection; the bot connects again."""
        loop, client = self._loop, self._client
        if loop is not None and client is not None:
            loop.call_soon_threadsafe(lambda: loop.create_task(client.disconnect(expected=False)))

    def stop(self, message: str = "Stopping") -> None:
        """Quit the server (with this quit message) and end the thread."""
        self._quit_message = message
        self._stopped.set()
        loop, wake = self._loop, self._wake
        if loop is not None and wake is not None and not loop.is_closed():
            loop.call_soon_threadsafe(wake.set)

    def stopped(self) -> bool:
        return self._stopped.is_set()

    def wait_until_ready(self, timeout: float | None = None) -> bool:
        """Block until the bot has connected and registered with the server."""
        return self._ready.wait(timeout)

    # ---- the thread ----------------------------------------------------------------------------

    def run(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception:
            log.exception("the bot stopped on an error")
        finally:
            self.connected = False
            self._stopped.set()

    async def _main(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._outgoing = asyncio.Queue()
        self._wake = asyncio.Event()
        writer = asyncio.create_task(self._writer())
        failures = 0
        try:
            while not self.stopped():
                self._wake.clear()
                client = _Client(
                    self, self.name, realname=self.desc, **{k: v for k, v in self._sasl.items() if v}
                )
                self._client = client
                try:
                    await asyncio.wait_for(
                        client.connect(
                            self.network,
                            self.port,
                            tls=self._tls,
                            tls_verify=self._tls_verify,
                            password=self._password,
                        ),
                        self._to_threshold,
                    )
                except (OSError, TimeoutError) as exc:
                    failures += 1
                    log.error("connection failed (%d of %d): %s", failures, self._retries, exc)
                    if failures >= self._retries:
                        log.error("reached the maximum number of retries")
                        return
                    await asyncio.sleep(self._retry_delay)
                    continue
                failures = 0
                self.connected = True
                self._ready.set()
                await self._wake.wait()  # until stop() or a disconnect
                self.connected = False
                self._ready.clear()
                if client.connected:
                    await client.quit(self._quit_message if self.stopped() else "Reconnecting")
                    await client.disconnect(expected=True)
                if not self.stopped():
                    await asyncio.sleep(1)
        finally:
            writer.cancel()

    def _client_disconnected(self) -> None:
        self.connected = False
        if self._wake is not None:
            self._wake.set()

    async def _writer(self) -> None:
        assert self._outgoing is not None  # noqa: S101 - set before this task starts
        while True:
            action = await self._outgoing.get()
            client = self._client
            if client is None or not client.connected:
                log.warning("not connected, dropped an outgoing message")
                continue
            try:
                await action(client)
            except OSError as exc:
                log.warning("sending failed, the connection is gone: %s", exc)
                if client.connected:
                    await client.disconnect(expected=False)
            except Exception:
                log.exception("sending failed")
            await asyncio.sleep(self._send_interval)

    def _send(self, action: Action) -> None:
        loop, outgoing = self._loop, self._outgoing
        if loop is None or outgoing is None or loop.is_closed():
            msg = "the bot is not running: call start() first"
            raise RuntimeError(msg)
        loop.call_soon_threadsafe(outgoing.put_nowait, action)

    # ---- incoming ------------------------------------------------------------------------------

    def _dispatch(self, message: Any) -> None:
        command = message.command
        command = str(command).zfill(3) if isinstance(command, int) else str(command).upper()
        params = [str(p) for p in message.params]
        sender = str(message.source or "").split("!", 1)[0]
        text = params[-1] if params else ""
        if command == "PRIVMSG" and text.startswith(ACTION_PREFIX) and text.endswith(CTCP_END):
            command, text = "ACTION", text[len(ACTION_PREFIX) : -1]
        callback = self._binds.get(command)
        if callback is not None:
            self._call(callback, sender, params[:-1], text)

    @staticmethod
    def _call(function: Callable[..., Any], *args: Any) -> None:
        try:
            function(*args)
        except Exception:
            log.exception("error in a callback")
