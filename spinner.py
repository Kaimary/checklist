import itertools
import sys
import threading
import time


class Spinner:
    """A simple spinner class"""

    def __init__(
        self,
        message: str = "Loading...",
        delay: float = 0.1,
        plain_output: bool = False,
    ) -> None:
        """Initialize the spinner class"""
        self._message = message
        self.delay = delay
        self.plain_output = plain_output
        self.spinner = itertools.cycle(["-", "/", "|", "\\"])
        self.running = False
        self.spinner_thread = None
        self.lock = threading.Lock()

    def spin(self) -> None:
        """Spin the spinner"""
        if self.plain_output:
            self.print_message()
            return
        while self.running:
            self.print_message()
            time.sleep(self.delay)

    def print_message(self):
        with self.lock:
            msg = self._message
        sys.stdout.write(f"\r{' ' * (len(msg) + 2)}\r")
        sys.stdout.write(f"{next(self.spinner)} {msg}\r")
        sys.stdout.flush()

    def set_message(self, message: str):
        """Update the spinner message"""
        with self.lock:
            self._message = message

    def start(self):
        self.running = True
        self.spinner_thread = threading.Thread(target=self.spin)
        self.spinner_thread.start()

    def stop(self):
        self.running = False
        if self.spinner_thread is not None:
            self.spinner_thread.join()
        with self.lock:
            msg = self._message
        sys.stdout.write(f"\r{' ' * (len(msg) + 2)}\r")
        sys.stdout.flush()

    def __enter__(self):
        """Start the spinner"""
        self.start()
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback) -> None:
        """Stop the spinner"""
        self.stop()
