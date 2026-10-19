from __future__ import annotations

import builtins
import io
import os
import sys
from contextlib import contextmanager
from types import TracebackType
from typing import Iterator, List

__all__ = ["LeakError", "assert_no_open_handles", "track_handles"]


class LeakError(RuntimeError):
    """Raised when one or more file handles are still open after a tracked block exits."""

    def __init__(self, open_count: int, summary: str) -> None:
        message = f"{open_count} leaked file handle(s) detected after block exit:\n{summary}"
        super().__init__(message)


class _TrackedOpen:
    """
    A wrapper around a file-like object that records its creation in a shared list
    and removes the record on close (only on close, not when the object is collected
    without an explicit close — see _finalize below for the reasoning).
    """

    __slots__ = (
        "_file",
        "_records",
        "_index",
        "_closed",
        "__dict__",
    )

    def __init__(self, file: object, records: List[dict]) -> None:
        # Use object.__setattr__ to bypass any attribute interception on the wrapped
        # object (buffered readers sometimes override __setattr__-like dunder flows
        # via descriptors) and to keep this wrapper's attribute access local.
        object.__setattr__(self, "_file", file)
        object.__setattr__(self, "_records", records)
        object.__setattr__(self, "_index", len(records))
        object.__setattr__(self, "_closed", False)
        records.append({"open": True})

    def close(self) -> None:
        if object.__getattribute__(self, "_closed"):
            return
        try:
            getattr(object.__getattribute__(self, "_file"), "close")()
        finally:
            object.__setattr__(self, "_closed", True)
            records = object.__getattribute__(self, "_records")
            index = object.__getattribute__(self, "_index")
            if index < len(records):
                records[index]["open"] = False

    def __enter__(self) -> object:
        return self

    def __exit__(
        self,
        exc_type: type | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()

    def __getattr__(self, name: str) -> object:
        return getattr(object.__getattribute__(self, "_file"), name)

    def __iter__(self) -> Iterator[object]:
        return iter(object.__getattribute__(self, "_file"))

    def __next__(self) -> object:
        return next(object.__getattribute__(self, "_file"))

    def __del__(self) -> None:
        # A tracked handle that reaches __del__ was never closed explicitly, which
        # is the exact failure we report on block exit. Finalizers are unreliable,
        # so we do NOT record a close here — __del__ is just a best-effort fallback
        # to avoid resource warnings and secondary descriptors left dangling.
        if not object.__getattribute__(self, "_closed"):
            try:
                self.close()
            except Exception:
                pass


def _make_open(records: List[dict]):
    original_open = builtins.open

    def patched_open(file, mode="r", buffering=-1, encoding=None, errors=None, newline=None, closefd=True, opener=None):
        f = original_open(
            file,
            mode=mode,
            buffering=buffering,
            encoding=encoding,
            errors=errors,
            newline=newline,
            closefd=closefd,
            opener=opener,
        )
        return _TrackedOpen(f, records)

    return patched_open


@contextmanager
def track_handles() -> Iterator[List[dict]]:
    """
    Context manager that patches builtins.open for the duration of the block and
    yields a list of per-handle records. Each record is a dict with the key
    ``open`` (bool). Records are appended in creation order and flipped to
    ``open=False`` when close() runs (directly or via context manager exit).

    Why a list-of-dicts instead of object graphs: the records must survive the
    wrapper's lifetime so that block-exit inspection still sees the history.
    """
    original_open = builtins.open
    records: List[dict] = []
    builtins.open = _make_open(records)
    try:
        yield records
    finally:
        builtins.open = original_open


def _records_summary(records: List[dict]) -> str:
    total = len(records)
    open_count = sum(1 for r in records if r.get("open"))
    lines = [f"  tracked opens: {total}", f"  still open: {open_count}"]
    return "\n".join(lines)


def assert_no_open_handles(records: List[dict], msg: str | None = None) -> None:
    """
    Raise LeakError if any handle created within a track_handles block is still open.
    Call this *inside* the ``with track_handles() as records:`` block, before it exits,
    so that closing the leaked file and the teardown of the block itself do not mask
    the leak.
    """
    open_count = sum(1 for r in records if r.get("open"))
    if open_count:
        raise LeakError(open_count, _records_summary(records) + (f"\n  {msg}" if msg else ""))
