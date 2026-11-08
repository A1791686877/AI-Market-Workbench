"""Cross-process Windows lock; lock files live beside E-drive state, never C."""
from contextlib import contextmanager
import msvcrt
import time


@contextmanager
def exclusive_lock(path, timeout=20):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        if stream.tell() == 0:
            stream.write(b'0'); stream.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() >= deadline: raise TimeoutError('State write lock timeout')
                time.sleep(.05)
        try:
            yield
        finally:
            stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
