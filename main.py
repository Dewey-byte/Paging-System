"""School Paging & Announcement Management System - application entry point."""
from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import sys
import traceback

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

from core import paths

log = logging.getLogger("main")


class SingleInstance(QObject):
    """Only one copy may run (two schedulers would play every bell twice)."""

    def __init__(self):
        super().__init__()
        user = "".join(ch for ch in getpass.getuser() if ch.isalnum()) or "user"
        self.name = f"{paths.APP_ID}-{user}"
        self.server: QLocalServer | None = None
        self.on_activate = None

    def acquire(self, restarting: bool = False) -> bool:
        if restarting:  # wait (up to 20 s) for the previous instance to exit
            import time
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                probe = QLocalSocket()
                probe.connectToServer(self.name)
                if not probe.waitForConnected(200):
                    break
                probe.disconnectFromServer()
                time.sleep(0.3)
        socket = QLocalSocket()
        socket.connectToServer(self.name)
        if socket.waitForConnected(300):
            socket.write(b"show")
            socket.flush()
            socket.waitForBytesWritten(300)
            socket.disconnectFromServer()
            return False
        QLocalServer.removeServer(self.name)
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._incoming)
        self.server.listen(self.name)
        return True

    def _incoming(self) -> None:
        connection = self.server.nextPendingConnection()
        if connection is not None:
            connection.waitForReadyRead(200)
            connection.deleteLater()
        if self.on_activate:
            self.on_activate()


class AppShell(QObject):
    """Switches between the sign-in window and the main window."""

    def __init__(self, app: QApplication, ctx):
        super().__init__()
        self.app = app
        self.ctx = ctx
        self.login = None
        self.main = None

    def show_login(self, message: str = "") -> None:
        from ui.login_window import LoginWindow
        self.login = LoginWindow(self.ctx)
        self.login.logged_in.connect(self.open_main)
        self.login.destroyed.connect(lambda *_: None)
        self.login.closeEvent = self._login_closed
        if message:
            self.login.error.set_text(message, "info")
            self.login.error.show()
        self.login.show()

    def _login_closed(self, event) -> None:
        if self.main is None:
            event.accept()
            self.app.quit()

    def open_main(self, user) -> None:
        from ui.main_window import MainWindow
        self.main = MainWindow(self.ctx)
        self.main.logout_requested.connect(self.logout)
        self.main.exit_requested.connect(self.exit)
        if self.login is not None:
            self.login.closeEvent = lambda e: e.accept()
            self.login.close()
            self.login.deleteLater()
            self.login = None
        if self.ctx.settings.get_bool("app.start_minimized") and self.main.tray is not None:
            self.main.hide()
        elif self.ctx.settings.get_bool("app.start_maximized"):
            self.main.showMaximized()
        else:
            self.main.show()

    def logout(self, reason: str) -> None:
        self.ctx.auth.logout(reason)
        main, self.main = self.main, None
        if main is not None:
            main.force_close()
            main.deleteLater()
        self.show_login(reason if "automatically" in reason else "")

    def exit(self) -> None:
        if self.main is not None:
            self.main.force_close()
        self.app.quit()

    def activate(self) -> None:
        window = self.main or self.login
        if window is not None:
            window.showNormal()
            window.raise_()
            window.activateWindow()


def parse_args(argv):
    parser = argparse.ArgumentParser(description=paths.APP_NAME)
    parser.add_argument("--data-dir", help="Use a custom data folder (database, audio, backups).")
    parser.add_argument("--simulated-audio", action="store_true", help="Never open real audio devices.")
    parser.add_argument("--restarting", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--self-test", metavar="FOLDER", nargs="?", const="",
                        help="Check every subsystem, write self_test.json and exit (0 = all OK).")
    return parser.parse_known_args(argv[1:])[0]


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv
    args = parse_args(argv)
    if args.self_test is not None:
        from core.diagnostics import run_self_test
        report = run_self_test(args.self_test or None)
        try:
            print(json.dumps(report, indent=2))
        except (OSError, ValueError):
            pass  # windowed executables have no console; the JSON file is written anyway
        return 0 if report["ok"] else 1
    if args.data_dir:
        paths.set_data_dir(args.data_dir)
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(argv)
    app.setApplicationName(paths.APP_NAME)
    app.setOrganizationName(paths.ORG_NAME)
    app.setApplicationVersion(paths.APP_VERSION)
    app.setQuitOnLastWindowClosed(False)

    from ui.branding import app_icon
    from ui.theme import theme
    app.setWindowIcon(app_icon())

    instance = SingleInstance()
    if not instance.acquire(restarting=args.restarting):
        return 0

    try:
        from core.app_context import AppContext
        ctx = AppContext(force_simulated_audio=True if args.simulated_audio else None)
        theme.apply(ctx.settings.get("app.theme") or "light")
        ctx.startup()
    except Exception as exc:  # pragma: no cover - startup failure path
        log.exception("Startup failed")
        QMessageBox.critical(None, paths.APP_NAME, f"The application could not start:\n\n{exc}\n\n"
                                                   f"{traceback.format_exc(limit=3)}")
        return 1

    shell = AppShell(app, ctx)
    instance.on_activate = shell.activate
    shell.show_login()
    code = app.exec()
    ctx.shutdown()
    return code


if __name__ == "__main__":
    sys.exit(main())
