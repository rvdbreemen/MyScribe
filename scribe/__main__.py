"""`python -m scribe` — start the app, localhost only (127.0.0.1:4242).

The browser is opened once uvicorn says it is listening - `Server.started`,
which uvicorn sets after the socket is bound and the lifespan startup
(migrate, reconcile, supervisor) has run - rather than after a guessed
delay. A start that fails never opens a tab onto a connection refused, and
`--no-browser` is for a terminal that would rather not have one at all.
"""

import argparse
import threading
import time
import webbrowser

import uvicorn

from scribe import env

HOST = "127.0.0.1"
DEFAULT_PORT = 4242

# How long the browser waits for the server to come up, and how often it
# looks. Thirty seconds is far beyond a normal start (well under one) but
# short enough that a machine with a stuck port does not keep a thread around.
BROWSER_WAIT_SECONDS = 30.0
BROWSER_POLL_SECONDS = 0.05


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scribe", description="MyScribe"
    )
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help=f"port on {HOST} (default {DEFAULT_PORT})"
    )
    parser.add_argument(
        "--no-supervisor",
        action="store_true",
        help="do not start the job supervisor thread, nor the watch folders and feeds"
        " that follow it: nothing is transcribed or looked at by itself",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="do not open the app in the default browser once it is listening",
    )
    return parser


def open_browser_when_ready(
    server: uvicorn.Server,
    url: str,
    *,
    poll: float = BROWSER_POLL_SECONDS,
    timeout: float = BROWSER_WAIT_SECONDS,
) -> threading.Thread:
    """Open ``url`` on a daemon thread once ``server.started`` is true.

    A server that gives up before starting sets ``should_exit`` (a signal
    arrived) or exits the process (the port was taken, the lifespan raised);
    a server that hangs runs into ``timeout``. In every one of those cases
    the thread ends without opening anything. Daemon, so the waiter never
    keeps a process alive that has nothing left to do.
    """

    def wait_then_open() -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if getattr(server, "should_exit", False):
                return
            if getattr(server, "started", False):
                webbrowser.open(url)
                return
            time.sleep(poll)

    thread = threading.Thread(target=wait_then_open, name="scribe-open-browser", daemon=True)
    thread.start()
    return thread


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    # `.env` first, before anything reads the environment: scribe.paths fixes
    # DATA_DIR from SCRIBE_DATA_DIR the moment it is imported, and importing
    # the app imports it. Hence the import below the call. bootstrap() is that
    # load plus this checkout's own tools on PATH, which the runner children
    # inherit.
    env.bootstrap()
    from scribe.app import create_app

    app = create_app(start_supervisor=not args.no_supervisor)
    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=args.port))
    if not args.no_browser:
        open_browser_when_ready(server, f"http://{HOST}:{args.port}/")
    server.run()


if __name__ == "__main__":
    main()
