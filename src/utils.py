import queue
import logging

log_queue: queue.Queue = queue.Queue()

def log(msg: str) -> None:
    print(msg)
    try:
        log_queue.put(msg)
    except Exception:
        pass


class TkLogHandler(logging.Handler):
    def emit(self, record):
        log(self.format(record))