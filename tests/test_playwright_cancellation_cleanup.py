"""Abandoned protocol futures, using real asyncio and no browser or driver."""

import asyncio
import gc
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from nano_batch.adapters.flow import FlowSession


# The offline suite does not require Playwright. Match its inspected exception
# identity; the separate installed-package probe uses the actual Connection.
TargetClosedError = type(
    "TargetClosedError", (Exception,), {"__module__": "playwright._impl._errors"},
)


class PlaywrightCancellationCleanupTests(unittest.TestCase):
    def run_disconnect(self, error_type=TargetClosedError, *, intentional=True,
                       already_done=False, cancelled=False, stop_error=False):
        loop = asyncio.new_event_loop()
        reports = []
        loop.set_exception_handler(lambda _, context: reports.append(context))
        future = loop.create_future()
        if cancelled:
            future.cancel()
        elif already_done:
            future.set_exception(error_type("synthetic-private-prompt"))
        callbacks = {"synthetic-rpc": SimpleNamespace(future=future, no_reply=False)}
        connection = SimpleNamespace(_callbacks=callbacks)

        # Cancelling an asyncio.wait task does not cancel its protocol future.
        async def abandon_wait():
            task = loop.create_task(asyncio.wait({future}))
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            return task.cancelled()

        self.assertTrue(loop.run_until_complete(abandon_wait()))

        def stop():
            connection._closed_error = error_type("synthetic-private-prompt")
            for callback in callbacks.values():
                if not callback.future.done():
                    callback.future.set_exception(connection._closed_error)
            callbacks.clear()
            loop.close()  # Queued done callbacks cannot be relied on after this.
            if stop_error:
                raise RuntimeError("synthetic-private-prompt")

        driver = SimpleNamespace(
            _impl_obj=SimpleNamespace(_connection=connection), stop=MagicMock(side_effect=stop),
        )
        session = FlowSession("https://example.invalid/synthetic", None)
        browser, cdp = MagicMock(), MagicMock()
        session._playwright, session._browser = driver, browser
        session._page_cdp_session = cdp
        if stop_error:
            with self.assertLogs("nano_batch.adapters.flow", level="ERROR") as logged:
                session.close(intentional_cancellation=intentional)
            self.assertNotIn("synthetic-private-prompt", " ".join(logged.output))
        else:
            session.close(intentional_cancellation=intentional)
        driver.stop.assert_called_once()
        if intentional:
            browser.close.assert_not_called()
            cdp.send.assert_not_called()
            cdp.detach.assert_not_called()
        else:
            browser.close.assert_called_once()
        future = None
        gc.collect()
        return reports

    def test_abandoned_protocol_future_is_observed_after_intentional_stop(self):
        self.assertEqual(self.run_disconnect(), [])

    def test_preexisting_target_closed_error_remains_reported(self):
        reports = self.run_disconnect(already_done=True)
        self.assertEqual(len(reports), 1)
        self.assertIsInstance(reports[0]["exception"], TargetClosedError)

    def test_cancelled_future_needs_no_exception_retrieval(self):
        self.assertEqual(self.run_disconnect(cancelled=True), [])

    def test_unrelated_protocol_exception_remains_reported(self):
        reports = self.run_disconnect(RuntimeError)
        self.assertEqual(len(reports), 1)
        self.assertIsInstance(reports[0]["exception"], RuntimeError)
        self.assertEqual(reports[0]["message"], "Future exception was never retrieved")

    def test_same_named_unrelated_exception_is_not_consumed(self):
        unrelated = type("TargetClosedError", (Exception,), {"__module__": "synthetic"})
        reports = self.run_disconnect(unrelated)
        self.assertEqual(len(reports), 1)
        self.assertIsInstance(reports[0]["exception"], unrelated)

    def test_normal_disconnect_does_not_hide_target_closed_errors(self):
        reports = self.run_disconnect(intentional=False)
        self.assertEqual(len(reports), 1)
        self.assertIsInstance(reports[0]["exception"], TargetClosedError)

    def test_stop_failure_still_observes_its_completed_protocol_future(self):
        self.assertEqual(self.run_disconnect(stop_error=True), [])

    def test_missing_protocol_registry_still_stops_without_browser_rpc(self):
        session = FlowSession("https://example.invalid/synthetic", None)
        driver, browser = SimpleNamespace(stop=MagicMock()), MagicMock()
        session._playwright, session._browser = driver, browser
        session.close(intentional_cancellation=True)
        driver.stop.assert_called_once()
        browser.close.assert_not_called()

    def test_unregistered_future_is_not_consumed_even_for_same_close_error(self):
        loop = asyncio.new_event_loop()
        reports = []
        loop.set_exception_handler(lambda _, context: reports.append(context))
        error = TargetClosedError("synthetic-private-prompt")
        future = loop.create_future()
        future.set_exception(error)
        connection = SimpleNamespace(_callbacks={}, _closed_error=error)
        session = FlowSession("https://example.invalid/synthetic", None)
        session._playwright = SimpleNamespace(
            _impl_obj=SimpleNamespace(_connection=connection), stop=loop.close,
        )
        session.close(intentional_cancellation=True)
        future = None
        gc.collect()
        self.assertEqual(len(reports), 1)
        self.assertIs(reports[0]["exception"], error)


if __name__ == "__main__":
    unittest.main()
