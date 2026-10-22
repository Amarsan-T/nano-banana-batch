"""Synthetic project setup only. No browser, driver, DOM, or endpoint access."""

from nano_batch.adapters.flow import FlowSession
from nano_batch.models import FlowProjectReference

PROJECT_ID = "00000000-0000-4000-8000-000000000001"
PROJECT = FlowProjectReference(PROJECT_ID, "https://flow.google.com/project/" + PROJECT_ID)
PRIVATE_DETAIL = "synthetic-private-prompt cookie=synthetic-token"


class SyntheticSetupSession(FlowSession):
    def __init__(self, profile, mode, *, failure_hook=lambda: None):
        super().__init__("https://flow.google.com/", profile)
        self.mode = mode
        self.failure_hook = failure_hook
        self.calls = dict.fromkeys(("create", "driver", "connect", "editor", "settings", "cleanup", "generate"), 0)

    def fail(self, kind=RuntimeError):
        self.failure_hook()
        raise kind(PRIVATE_DETAIL)

    def create_flow_project(self):
        self.calls["create"] += 1
        if self.mode == "project":
            self.fail()
        return PROJECT

    def _playwright_api(self):
        session = self
        class Page:
            url = PROJECT.project_url
            @property
            def context(self):
                return Context()
        class CDP:
            def detach(self): pass
            def send(self, *_args): pass
        class Context:
            pages = [Page()]
            def new_cdp_session(self, _page): return CDP()
        class Browser:
            contexts = [Context()]
            def close(self):
                session.calls["cleanup"] += 1
                if session.mode == "cleanup": session.fail()
        class BrowserType:
            def connect_over_cdp(self, *_args, **_kwargs):
                session.calls["connect"] += 1
                if session.mode == "reconnect": session.fail(OSError)
                return Browser()
        class Playwright:
            chromium = BrowserType()
            def stop(self): pass
        class Manager:
            def start(self):
                session.calls["driver"] += 1
                if session.mode == "driver": session.fail(ImportError)
                return Playwright()
        return Manager

    def _verify_live_background_flags(self): pass
    def _enable_page_focus_emulation(self, _page): pass
    def _enable_page_lifecycle_keepalive(self): pass
    def ensure_production_window_usable(self, **_kwargs): pass
    def _wait_for_saved_project_reference(self, _project): pass

    def _wait_for_authenticated_image_editor(self, _project):
        self.calls["editor"] += 1
        if self.mode == "editor": self.fail()
        return object()

    def configure_generation(self, *_args, **_kwargs):
        self.calls["settings"] += 1
        if self.mode == "settings": self.fail(ValueError)

    def generate_one(self, **_kwargs):
        self.calls["generate"] += 1
        self.fail()

    def generate_one_with_unusual_activity_recovery(self, **kwargs):
        # The runner selects this inherited Flow entry point when a project is
        # bound. Keep the generation control synthetic too, with no real open().
        return self.generate_one(**kwargs)
