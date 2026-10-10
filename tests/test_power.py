import inspect
import unittest
from unittest.mock import MagicMock, patch

from nano_batch.power import (
    ES_CONTINUOUS,
    ES_DISPLAY_REQUIRED,
    ES_SYSTEM_REQUIRED,
    WindowsExecutionStateError,
    windows_execution_state_guard,
)
import nano_batch.power as power_module


class WindowsExecutionStateGuardTests(unittest.TestCase):
    def test_guard_requests_system_and_display_and_clears_on_success(self):
        setter = MagicMock(return_value=1)

        with patch("nano_batch.power._is_windows", return_value=True):
            with windows_execution_state_guard(setter=setter) as active:
                self.assertTrue(active)

        self.assertEqual(
            [call.args[0] for call in setter.call_args_list],
            [
                ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED,
                ES_CONTINUOUS,
            ],
        )

    def test_guard_clears_on_exception(self):
        setter = MagicMock(return_value=1)

        with patch("nano_batch.power._is_windows", return_value=True), self.assertRaisesRegex(
            RuntimeError, "batch failed"
        ):
            with windows_execution_state_guard(setter=setter):
                raise RuntimeError("batch failed")

        setter.assert_called_with(ES_CONTINUOUS)
        self.assertEqual(setter.call_count, 2)

    def test_guard_clears_on_keyboard_interrupt(self):
        setter = MagicMock(return_value=1)

        with patch("nano_batch.power._is_windows", return_value=True), self.assertRaises(
            KeyboardInterrupt
        ):
            with windows_execution_state_guard(setter=setter):
                raise KeyboardInterrupt()

        setter.assert_called_with(ES_CONTINUOUS)
        self.assertEqual(setter.call_count, 2)

    def test_guard_is_noop_off_windows_or_when_disabled(self):
        setter = MagicMock(return_value=1)
        with patch("nano_batch.power._is_windows", return_value=False):
            with windows_execution_state_guard(setter=setter) as active:
                self.assertFalse(active)
        with patch("nano_batch.power._is_windows", return_value=True):
            with windows_execution_state_guard(enabled=False, setter=setter) as active:
                self.assertFalse(active)
        setter.assert_not_called()

    def test_guard_fails_before_batch_when_windows_rejects_request(self):
        setter = MagicMock(return_value=0)
        with patch("nano_batch.power._is_windows", return_value=True), self.assertRaises(
            WindowsExecutionStateError
        ):
            with windows_execution_state_guard(setter=setter):
                self.fail("guard must not enter the production batch")

    def test_guard_uses_no_fake_keyboard_or_mouse_input(self):
        source = inspect.getsource(power_module).casefold()
        self.assertNotIn("sendinput", source)
        self.assertNotIn("keybd_event", source)
        self.assertNotIn("mouse_event", source)
