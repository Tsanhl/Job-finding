"""Synthetic fixtures only. Never attach to a real assessment or call an API."""
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.assessment_monitor import Answer, AssessmentMonitor, BrowserCapture, Capture, solve_capture


class Source:
    def __init__(self):
        self.image = b"synthetic-panel-one"

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def capture(self):
        return Capture(self.image)


def wait_for(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("Worker did not reach expected state")
        time.sleep(0.005)


class MonitorTests(unittest.TestCase):
    def make_worker(self, source, solve, **kwargs):
        worker = AssessmentMonitor(lambda: source, solve, interval=0.01, **kwargs)
        self.addCleanup(lambda: (worker.stop(), worker.join(2)))
        worker.start()
        return worker

    def test_unchanged_screen_is_solved_once_then_completion_stops(self):
        source = Source()
        calls = []
        def solve(capture):
            calls.append(capture.fingerprint)
            return Answer("answer", "Synthetic option") if len(calls) == 1 else Answer("complete")
        worker = self.make_worker(source, solve)
        wait_for(lambda: worker.view()[1] is not None)
        time.sleep(0.05)
        self.assertEqual(len(calls), 1)
        source.image = b"synthetic-completion-panel"
        worker.join(1)
        self.assertFalse(worker.view()[3])
        self.assertEqual(worker.view()[0], "Assessment complete; answer cleared")
        self.assertIsNone(worker.view()[1])

    def test_stop_during_inference_clears_and_cannot_republish(self):
        entered, release = threading.Event(), threading.Event()
        def solve(_):
            entered.set()
            release.wait(1)
            return Answer("answer", "Synthetic answer")
        worker = self.make_worker(Source(), solve)
        self.assertTrue(entered.wait(1))
        worker.stop()
        release.set()
        worker.join(1)
        self.assertIsNone(worker.view()[1])
        self.assertIsNone(worker.source_factory)

    def test_stale_answer_is_discarded(self):
        source = Source()
        def solve(_):
            source.image = b"synthetic-second-panel"
            return Answer("answer", "Old option")
        worker = self.make_worker(source, solve, max_requests=1)
        worker.join(1)
        self.assertIsNone(worker.view()[1])
        self.assertIn("request limit", worker.view()[0])

    def test_exception_does_not_expose_content(self):
        def solve(_):
            raise RuntimeError("synthetic-private-content")
        worker = self.make_worker(Source(), solve)
        worker.join(1)
        self.assertNotIn("synthetic-private-content", str(worker.view()))
        self.assertIsNone(worker.view()[1])

    def test_disconnected_ui_expires_lease(self):
        worker = self.make_worker(Source(), lambda _: Answer("answer", "Synthetic"), lease_seconds=0.03)
        worker.join(1)
        self.assertIn("disconnected", worker.view()[0])
        self.assertIsNone(worker.view()[1])

    def test_api_request_disables_response_storage(self):
        client = MagicMock()
        client.responses.create.return_value = SimpleNamespace(
            output_text='{"status":"answer","answer":"Synthetic","explanation":"Example"}'
        )
        with patch("openai.OpenAI") as constructor:
            constructor.return_value.__enter__.return_value = client
            result = solve_capture(Capture(b"synthetic-image"), "example-model")
        request = client.responses.create.call_args.kwargs
        self.assertFalse(request["store"])
        self.assertNotIn("previous_response_id", request)
        self.assertEqual(result.answer, "Synthetic")
        self.assertNotIn("synthetic-image", repr(Capture(b"synthetic-image")))

    def test_capture_is_in_memory_and_detach_leaves_browser_open(self):
        page = MagicMock()
        page.url = "https://example.test/practice"
        page.is_closed.return_value = False
        region = page.locator.return_value
        region.count.return_value = 1
        region.is_visible.return_value = True
        region.screenshot.return_value = b"synthetic-png"
        runtime = MagicMock()
        browser = runtime.chromium.connect_over_cdp.return_value
        browser.contexts = [SimpleNamespace(pages=[page])]
        with patch("playwright.sync_api.sync_playwright") as playwright:
            playwright.return_value.start.return_value = runtime
            with BrowserCapture(page.url, "#synthetic-panel") as source:
                self.assertEqual(source.capture().image, b"synthetic-png")
                page.url = "https://example.test/different"
                with self.assertRaises(ValueError):
                    source.capture()
        self.assertNotIn("path", region.screenshot.call_args.kwargs)
        browser.close.assert_not_called()
        page.close.assert_not_called()
        runtime.stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
