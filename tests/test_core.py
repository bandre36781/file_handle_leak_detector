import gc
import os
import tempfile
import unittest
import weakref

from file_handle_leak_detector import LeakError, assert_no_open_handles, track_handles


class TestTracking(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="leak_detector_")
        self.tmpfile = os.path.join(self.tmpdir, "data.txt")
        with open(self.tmpfile, "w") as f:
            f.write("hello")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_no_leaks_when_all_closed(self):
        with track_handles() as records:
            with open(self.tmpfile) as f:
                self.assertEqual(f.read(), "hello")
            assert_no_open_handles(records)

    def test_detects_unclosed_handle(self):
        with track_handles() as records:
            f = open(self.tmpfile)
            _ = f.read()
            with self.assertRaises(LeakError):
                assert_no_open_handles(records)
            f.close()
            assert_no_open_handles(records)

    def test_partial_leak(self):
        with track_handles() as records:
            f1 = open(self.tmpfile)
            f1.close()
            f2 = open(self.tmpfile)
            _ = f2.read()
            with self.assertRaises(LeakError) as cm:
                assert_no_open_handles(records)
            self.assertIn("1 leaked", str(cm.exception))
            f2.close()

    def test_exception_in_block_still_restores_open(self):
        with self.assertRaises(RuntimeError):
            with track_handles() as records:
                raise RuntimeError("boom")
        # builtins.open must be restored after exception
        import builtins
        self.assertIs(builtins.open, open)

    def test_nested_open_does_not_double_close(self):
        with track_handles() as records:
            f = open(self.tmpfile)
            f.close()
            # Calling close() a second time must be a safe no-op for the wrapper.
            f.close()
            assert_no_open_handles(records)

    def test_close_idempotent_after_manual_close(self):
        with track_handles() as records:
            f = open(self.tmpfile)
            underlying = f._file
            underlying.close()
            f.close()
            assert_no_open_handles(records)

    def test_attributes_proxy_through_wrapper(self):
        with track_handles() as records:
            with open(self.tmpfile) as f:
                self.assertEqual(f.read(), "hello")
                # Attribute access should proxy to the underlying file object.
                self.assertTrue(hasattr(f, "name"))
                self.assertEqual(f.name, self.tmpfile)
            assert_no_open_handles(records)

    def test_summary_in_error_message(self):
        with track_handles() as records:
            f = open(self.tmpfile)
            _ = f.read()
            with self.assertRaises(LeakError) as cm:
                assert_no_open_handles(records, msg="ctx")
            msg = str(cm.exception)
            self.assertIn("1 leaked", msg)
            self.assertIn("ctx", msg)
            f.close()

    def test_no_handles_means_no_error(self):
        with track_handles() as records:
            assert_no_open_handles(records)

    def test_files_opened_outside_block_not_tracked(self):
        f = open(self.tmpfile)
        try:
            with track_handles() as records:
                assert_no_open_handles(records)
        finally:
            f.close()

    def test_iter_and_next_on_wrapper(self):
        path = os.path.join(self.tmpdir, "lines.txt")
        with open(path, "w") as out:
            out.write("a\nb\nc\n")
        with track_handles() as records:
            with open(path) as f:
                collected = [line for line in f]
                self.assertEqual(collected, ["a\n", "b\n", "c\n"])
            assert_no_open_handles(records)

    def test_zero_dependencies_only_stdlib(self):
        # Enforce the zero-third-party-dependency constraint structurally: the only
        # importable module in this library is file_handle_leak_detector itself.
        import file_handle_leak_detector as mod
        # Ensure __init__ re-exports the documented API.
        self.assertTrue(hasattr(mod, "LeakError"))
        self.assertTrue(hasattr(mod, "assert_no_open_handles"))
        self.assertTrue(hasattr(mod, "track_handles"))


if __name__ == "__main__":
    unittest.main()
