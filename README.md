# file_handle_leak_detector

A small test helper that patches `builtins.open` for the duration of a block and fails the test if any file handle created inside that block is still open when you ask.

```python
import os
import tempfile
from file_handle_leak_detector import track_handles, assert_no_open_handles, LeakError

with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as tmp:
    tmp.write("data")
path = tmp.name

with track_handles() as records:
    with open(path) as f:
        _ = f.read()
    assert_no_open_handles(records)  # raises LeakError if anything is still open

os.unlink(path)
```

## Why this exists

Library code that opens files and forgets to close them tends to fail tests only on some platforms, under high fd pressure, or when the GC runs late. This module surfaces those leaks deterministically: during a `track_handles()` block, every `open()` call returns a thin wrapper that records its lifetime and reports `open=True` until `close()` runs. `assert_no_open_handles(records)` flips the test red the instant a leak exists, before the block tears down and finalizers mask it.

The trade-off: the wrapper replaces `builtins.open` for the whole interpreter for the block's duration. It is not safe to use across threads. It is intended for single-threaded test suites, not production code.

## The awkward edge

Call `assert_no_open_handles(records)` **inside** the `with track_handles()` block, not after it. A tracked handle that is never closed will still be cleaned up by Python's finalizer when the block exits and the wrapper is collected — but by then the test has lost the chance to report the leak cleanly. The assertion is designed to run before that happens, while the records still reflect the truth. The same reasoning applies to handles closed by CPython's reference-counting `__del__` rather than by explicit `close()`: such a handle is still considered leaked because the contract under test is explicit close, not finalization.

## Design notes

The window stores values eagerly rather than keeping running aggregates. Running
sums drift with floating point over long streams, and recomputing from a small
buffer is cheap enough that the drift is not worth the speed.

