"""A tiny IRC server on localhost, just enough protocol for pydle to connect, register and WHOIS."""

import contextlib
import socket
import struct
import threading
import time

from pydle.features.rfc1459.parsing import RFC1459Message


def parse(line):
    """(COMMAND, (params...)): IRC allows 'PRIVMSG #c two' and 'PRIVMSG #c :two', so compare parsed lines."""
    message = RFC1459Message.parse(line.encode("utf-8") + b"\r\n")
    return str(message.command).upper(), tuple(message.params)


class FakeIrcServer:
    def __init__(self, registered_nicks=()):
        self.registered_nicks = set(registered_nicks)
        self.lines = []  # everything the bot sent, in order, across connections
        self.connections = 0
        self._lock = threading.RLock()
        self._conn = None
        self._drop = None
        self._listener = socket.socket()
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(5)
        self.port = self._listener.getsockname()[1]
        self._closing = False
        threading.Thread(target=self._accept_loop, daemon=True).start()

    # ---- what a test uses ----------------------------------------------------------------------

    def send(self, line):
        self._conn.sendall(line.encode("utf-8") + b"\r\n")

    def drop(self, reset=False):
        """Close the current connection without a word: a clean close, or a TCP reset (an abrupt drop)."""
        self._drop = "reset" if reset else "close"

    def wait_for(self, predicate, timeout=15):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self._lock:
                for line in self.lines:
                    if predicate(line):
                        return line
            time.sleep(0.02)
        return None

    def wait_for_command(self, command, *params, timeout=15):
        return self.wait_for(lambda line: parse(line) == (command, params), timeout)

    def commands(self, *names):
        with self._lock:
            parsed = [parse(line) for line in self.lines]
        return [p for p in parsed if not names or p[0] in names]

    def count(self, predicate):
        with self._lock:
            return sum(1 for line in self.lines if predicate(line))

    def close(self):
        self._closing = True
        for sock in (self._conn, self._listener):
            if sock:
                with contextlib.suppress(OSError):
                    sock.close()

    # ---- the protocol --------------------------------------------------------------------------

    def _accept_loop(self):
        while not self._closing:
            try:
                conn, _ = self._listener.accept()
            except OSError:
                return
            self._conn = conn
            self.connections += 1
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        buffer = b""
        nick = "*"
        conn.settimeout(0.05)  # so a requested drop is noticed; closing from another thread would not work
        try:
            while True:
                if self._drop and conn is self._conn:
                    if self._drop == "reset":
                        conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                    self._drop = None
                    conn.close()
                    return
                try:
                    chunk = conn.recv(4096)
                except TimeoutError:
                    continue
                if not chunk:
                    return
                buffer += chunk
                *lines, buffer = buffer.split(b"\r\n")
                decoded = [raw.decode("utf-8", errors="replace") for raw in lines]
                # Record everything received before replying: a reply can fail if the bot has hung up.
                with self._lock:
                    self.lines.extend(decoded)
                for line in decoded:
                    nick = self._reply(conn, line, nick)
        except OSError:
            return

    def _reply(self, conn, line, nick):
        def say(text):
            with contextlib.suppress(OSError):  # the bot may already have gone
                conn.sendall(text.encode("utf-8") + b"\r\n")

        parts = line.split(" ")
        command = parts[0].upper()
        if command == "CAP" and parts[1] == "LS":
            say(":irc.test CAP * LS :")
        elif command == "NICK":
            nick = parts[1]
        elif command == "USER":
            say(f":irc.test 001 {nick} :Welcome")
            say(f":irc.test 375 {nick} :- irc.test Message of the day -")
            say(f":irc.test 376 {nick} :End of /MOTD command.")
        elif command == "PING":
            say(f":irc.test PONG irc.test {parts[1]}")
        elif command == "WHOIS":
            target = parts[1]
            say(f":irc.test 311 {nick} {target} user host * :Real Name")
            if target in self.registered_nicks:
                say(f":irc.test 307 {nick} {target} :has identified for this nick")
            say(f":irc.test 318 {nick} {target} :End of /WHOIS list.")
        return nick
