"""Abandoned protocol futures, using real asyncio and no browser or driver."""

import asyncio
import gc
import logging
import signal
import unittest
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import app
from nano_batch.adapters.flow import FlowSession
from nano_batch.cancellation import authoritative_cancellation_scope


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


class TeardownCancellationTransitionTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.session = FlowSession("https://example.invalid/synthetic", None)
        self.cdp, self.browser = MagicMock(), MagicMock()
        self.driver = SimpleNamespace(stop=MagicMock())
        self.session.page = MagicMock()
        self.session._page_cdp_session = self.cdp
        self.session._focus_emulation_enabled = True
        self.session._browser, self.session._playwright = self.browser, self.driver
        self.operations = (
            ("focus_reset", self.cdp.send), ("detach", self.cdp.detach),
            ("browser_disconnect", self.browser.close), ("driver_stop", self.driver.stop),
        )
        for name, operation in self.operations:
            operation.side_effect = lambda *a, _name=name, **k: self.events.append(_name)
        self.original_filters = list(logging.getLogger("asyncio").filters)

    def assert_released(self):
        for name in ("page", "_page_cdp_session", "_browser", "_playwright"):
            self.assertIsNone(getattr(self.session, name))
        self.assertFalse(self.session._focus_emulation_enabled)
        self.assertEqual(logging.getLogger("asyncio").filters, self.original_filters)
        self.driver.stop.assert_called_once_with()

    def interrupt_at(self, target, mode):
        operation = dict(self.operations)[target]

        def interrupted(*args, **kwargs):
            self.events.append(target)
            try:
                signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
            except KeyboardInterrupt:
                if mode == "direct":
                    raise
                if mode == "translated":
                    raise RuntimeError("synthetic translated interrupt")

        operation.side_effect = interrupted

    def transition_cases(self, target):
        for mode in ("direct", "translated", "swallowed"):
            with self.subTest(mode=mode):
                fixture = type(self)(methodName="runTest")
                fixture.setUp()
                fixture.interrupt_at(target, mode)
                with authoritative_cancellation_scope() as state:
                    with self.assertRaises(KeyboardInterrupt):
                        fixture.session.__exit__(None, None, None)
                    names = [name for name, _ in fixture.operations]
                    expected = names[:names.index(target) + 1]
                    if target != "driver_stop":
                        expected.append("driver_stop")
                    self.assertEqual(fixture.events, expected)
                    self.assertEqual(state.interrupt_signal_count, 1)
                    self.assertEqual(state.received_phase, "session_cleanup")
                    self.assertTrue(state.cleanup_started)
                    self.assertTrue(state.cleanup_completed)
                fixture.assert_released()

    def test_cancel_before_teardown_skips_all_browser_rpcs(self):
        with authoritative_cancellation_scope() as state:
            state.checkpoint("generation_result_wait")
            state.request()
            with self.assertRaises(KeyboardInterrupt):
                self.session.__exit__(None, None, None)
            self.assertEqual(state.interrupt_signal_count, 1)
            self.assertEqual(state.received_phase, "generation_result_wait")
        self.assertEqual(self.events, ["driver_stop"])
        self.assert_released()

    def test_close_uses_existing_latch_without_an_explicit_cancellation_argument(self):
        with authoritative_cancellation_scope() as state:
            state.checkpoint("generation_result_wait")
            state.request()
            self.session.close()
            self.assertEqual(state.interrupt_signal_count, 1)
            self.assertEqual(state.received_phase, "generation_result_wait")
            self.assertTrue(state.cleanup_completed)
        self.assertEqual(self.events, ["driver_stop"])
        self.assert_released()

    def test_cancel_during_focus_reset_skips_detach_and_browser_disconnect(self):
        self.transition_cases("focus_reset")

    def test_cancel_during_detach_skips_browser_disconnect(self):
        self.transition_cases("detach")

    def test_cancel_during_browser_disconnect_still_stops_driver_once(self):
        self.transition_cases("browser_disconnect")

    def test_cancel_during_driver_stop_remains_the_first_interrupt(self):
        self.transition_cases("driver_stop")

    def test_normal_teardown_keeps_existing_operation_order(self):
        self.session.__exit__(None, None, None)
        self.assertEqual(self.events, [name for name, _ in self.operations])
        self.cdp.send.assert_called_once_with("Emulation.setFocusEmulationEnabled", {"enabled": False})
        self.assert_released()

    def test_repeated_normal_close_does_not_repeat_resource_operations(self):
        self.session.close()
        self.session.close()
        self.assertEqual(self.events, [name for name, _ in self.operations])
        self.assert_released()

    def test_repeated_cancelled_close_preserves_first_signal_and_releases_filter(self):
        self.interrupt_at("detach", "direct")
        with authoritative_cancellation_scope() as state:
            with self.assertRaises(KeyboardInterrupt):
                self.session.__exit__(None, None, None)
            first_phase = state.received_phase
            self.session.close(intentional_cancellation=True)
            self.session.close(intentional_cancellation=True)
            self.assertEqual(state.interrupt_signal_count, 1)
            self.assertEqual(state.received_phase, first_phase)
        self.assertEqual(self.events, ["focus_reset", "detach", "driver_stop"])
        self.assert_released()

    def test_direct_keyboard_interrupt_without_a_latch_still_skips_browser_calls(self):
        self.cdp.send.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            self.session.__exit__(None, None, None)
        self.cdp.detach.assert_not_called()
        self.browser.close.assert_not_called()
        self.assert_released()

    def test_unrelated_browser_exception_still_propagates_and_stops_driver(self):
        error = RuntimeError("synthetic browser disconnect error")
        self.browser.close.side_effect = error
        with self.assertRaises(RuntimeError) as raised:
            self.session.close()
        self.assertIs(raised.exception, error)
        self.assert_released()

    def test_unrelated_driver_exception_still_propagates(self):
        error = RuntimeError("synthetic driver stop error")
        self.driver.stop.side_effect = error
        with self.assertRaises(RuntimeError) as raised:
            self.session.close()
        self.assertIs(raised.exception, error)
        self.assert_released()

    def test_stop_error_during_transition_is_reported_without_replacing_interrupt(self):
        self.interrupt_at("detach", "direct")
        self.driver.stop.side_effect = RuntimeError("synthetic-private-prompt")
        with authoritative_cancellation_scope() as state:
            with self.assertLogs("nano_batch.adapters.flow", level="ERROR") as captured:
                with self.assertRaises(KeyboardInterrupt):
                    self.session.__exit__(None, None, None)
            self.assertEqual(state.interrupt_signal_count, 1)
            self.assertEqual(state.cleanup_error_type, "RuntimeError")
        self.assertIn("RuntimeError", " ".join(captured.output))
        self.assertNotIn("synthetic-private-prompt", " ".join(captured.output))
        self.browser.close.assert_not_called()
        self.assert_released()

    def test_existing_best_effort_cdp_errors_do_not_skip_normal_disconnect(self):
        self.cdp.send.side_effect = RuntimeError("synthetic focus reset error")
        self.cdp.detach.side_effect = RuntimeError("synthetic detach error")
        self.session.close()
        self.browser.close.assert_called_once_with()
        self.assert_released()

    def future_transition(self, error_type, *, during_stop=False):
        loop = asyncio.new_event_loop()
        reports = []
        loop.set_exception_handler(lambda _, context: reports.append(context))
        future = loop.create_future()
        connection = SimpleNamespace(_callbacks={"synthetic": SimpleNamespace(future=future)})
        self.driver._impl_obj = SimpleNamespace(_connection=connection)

        def stop():
            self.events.append("driver_stop")
            connection._closed_error = error_type("synthetic-private-prompt")
            future.set_exception(connection._closed_error)
            connection._callbacks.clear()
            loop.close()
            if during_stop:
                signal.getsignal(signal.SIGINT)(signal.SIGINT, None)

        self.driver.stop.side_effect = stop
        if not during_stop:
            self.interrupt_at("detach", "direct")
        with authoritative_cancellation_scope(), self.assertRaises(KeyboardInterrupt):
            self.session.__exit__(None, None, None)
        self.assert_released()
        future = None
        gc.collect()
        return reports

    def test_transition_observes_only_the_owned_shutdown_error(self):
        self.assertEqual(self.future_transition(TargetClosedError), [])

    def test_transition_keeps_unrelated_future_exception_visible(self):
        reports = self.future_transition(RuntimeError)
        self.assertEqual(len(reports), 1)
        self.assertIsInstance(reports[0]["exception"], RuntimeError)

    def test_signal_during_normal_stop_preserves_shutdown_future_handling(self):
        self.assertEqual(self.future_transition(TargetClosedError, during_stop=True), [])

    def test_translated_teardown_interrupt_preserves_cli_exit_130_and_message(self):
        self.interrupt_at("detach", "translated")
        args = SimpleNamespace(command="generate-one", func=lambda _: self.session.__exit__(None, None, None) or 0)
        output = StringIO()
        with patch.object(app, "build_parser") as parser, redirect_stdout(output):
            parser.return_value.parse_args.return_value = args
            self.assertEqual(app.main(), 130)
        self.assertEqual(output.getvalue(), "\nInterrupted.\n")
        self.browser.close.assert_not_called()
        self.assert_released()


if __name__ == "__main__":
    unittest.main()
