import atexit
import ctypes
import os
import sys
import threading
import webbrowser
from pathlib import Path

from werkzeug.serving import make_server


APP_HOST = "0.0.0.0"
APP_PORT = 5000
APP_URL = f"http://127.0.0.1:{APP_PORT}/"
INSTANCE_MUTEX_NAME = "Local\\AniBaseSingleInstance"
ERROR_ALREADY_EXISTS = 183
WAIT_ABANDONED = 0x00000080
WAIT_OBJECT_0 = 0x00000000
INFINITE = 0xFFFFFFFF
SHUTDOWN_JOIN_TIMEOUT_SECONDS = 5

INSTANCE_MUTEX_HANDLE = None
INSTANCE_LOCK_FILE = None

app = None
app_log = None
RESOURCE_DIR = None
load_settings = None
log_startup_summary = None
sync_all_library = None
start_scanner = None
periodic_sync_task = None
start_auto_import_worker = None
request_shutdown = None
stop_library_observer = None
join_background_workers = None

flask_server = None
flask_thread = None
scanner_thread = None
sync_thread = None


def get_fallback_log_file():
    if os.name != "nt":
        data_home = os.environ.get("XDG_DATA_HOME", "").strip()
        if not data_home:
            data_home = os.path.join(os.path.expanduser("~"), ".local", "share")
        log_dir = os.path.join(data_home, "AniBase", "logs")
        os.makedirs(log_dir, exist_ok=True)
        return os.path.join(log_dir, "anibase.log")

    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if not local_app_data:
        local_app_data = os.path.join(os.path.expanduser("~"), "AppData", "Local")
    log_dir = os.path.join(local_app_data, "AniBase", "logs")
    os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir, "anibase.log")


def fallback_log(message):
    try:
        with open(get_fallback_log_file(), "a", encoding="utf-8") as handle:
            handle.write(f"{message}\n")
    except OSError:
        pass


def release_instance_mutex():
    global INSTANCE_MUTEX_HANDLE, INSTANCE_LOCK_FILE
    if INSTANCE_LOCK_FILE is not None:
        try:
            import fcntl
            fcntl.flock(INSTANCE_LOCK_FILE, fcntl.LOCK_UN)
            INSTANCE_LOCK_FILE.close()
        finally:
            INSTANCE_LOCK_FILE = None
    if not INSTANCE_MUTEX_HANDLE:
        return

    try:
        ctypes.windll.kernel32.ReleaseMutex(INSTANCE_MUTEX_HANDLE)
        ctypes.windll.kernel32.CloseHandle(INSTANCE_MUTEX_HANDLE)
    finally:
        INSTANCE_MUTEX_HANDLE = None


def acquire_single_instance_guard():
    global INSTANCE_MUTEX_HANDLE, INSTANCE_LOCK_FILE

    if os.name != "nt":
        import fcntl
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "").strip()
        lock_path = Path(runtime_dir or Path.home() / ".cache") / "anibase.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        INSTANCE_LOCK_FILE = lock_path.open("a+")
        try:
            fcntl.flock(INSTANCE_LOCK_FILE, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            INSTANCE_LOCK_FILE.close()
            INSTANCE_LOCK_FILE = None
            webbrowser.open(APP_URL)
            return False
        atexit.register(release_instance_mutex)
        return True

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_bool,
        ctypes.c_wchar_p,
    ]
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]

    handle = kernel32.CreateMutexW(None, False, INSTANCE_MUTEX_NAME)
    error_code = kernel32.GetLastError()
    if not handle:
        fallback_log(f"Single-instance mutex could not be created. error={error_code}")
        return False

    if error_code == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        webbrowser.open(APP_URL)
        return False

    wait_result = kernel32.WaitForSingleObject(handle, INFINITE)
    if wait_result not in {WAIT_OBJECT_0, WAIT_ABANDONED}:
        kernel32.CloseHandle(handle)
        fallback_log(f"Single-instance mutex could not be acquired. result={wait_result}")
        return False

    INSTANCE_MUTEX_HANDLE = handle
    atexit.register(release_instance_mutex)
    return True


def load_application_components():
    global app, app_log, RESOURCE_DIR, load_settings, log_startup_summary
    global sync_all_library, start_scanner, periodic_sync_task, start_auto_import_worker
    global request_shutdown, stop_library_observer, join_background_workers

    from main import (
        app as flask_app,
        app_log as main_app_log,
        RESOURCE_DIR as main_resource_dir,
        load_settings as main_load_settings,
        log_startup_summary as main_log_startup_summary,
        sync_all_library as main_sync_all_library,
        start_scanner as main_start_scanner,
        periodic_sync_task as main_periodic_sync_task,
        start_auto_import_worker as main_start_auto_import_worker,
        request_shutdown as main_request_shutdown,
        stop_library_observer as main_stop_library_observer,
        join_background_workers as main_join_background_workers,
    )

    app = flask_app
    app_log = main_app_log
    RESOURCE_DIR = main_resource_dir
    load_settings = main_load_settings
    log_startup_summary = main_log_startup_summary
    sync_all_library = main_sync_all_library
    start_scanner = main_start_scanner
    periodic_sync_task = main_periodic_sync_task
    start_auto_import_worker = main_start_auto_import_worker
    request_shutdown = main_request_shutdown
    stop_library_observer = main_stop_library_observer
    join_background_workers = main_join_background_workers


def run_flask():
    global flask_server

    flask_server = make_server(
        host=APP_HOST,
        port=APP_PORT,
        app=app,
        threaded=True,
    )
    flask_server.serve_forever()


def shutdown_flask_server():
    if flask_server is None:
        return

    try:
        flask_server.shutdown()
    except Exception as error:
        app_log(f"Flask server shutdown failed: {error}", "ERROR")


def start_background_services():
    global flask_thread, scanner_thread, sync_thread

    flask_thread = threading.Thread(
        target=run_flask,
        name="tray-server",
        daemon=True,
    )
    flask_thread.start()

    scanner_thread = threading.Thread(
        target=start_scanner,
        name="library-watchdog",
        daemon=True,
    )
    scanner_thread.start()

    sync_thread = threading.Thread(
        target=periodic_sync_task,
        name="periodic-sync",
        daemon=True,
    )
    sync_thread.start()

    start_auto_import_worker()
    log_startup_summary(
        mode="tray",
        host=APP_HOST,
        port=APP_PORT,
        scanner_enabled=True,
        periodic_sync_enabled=True,
        auto_import_worker_enabled=True,
    )


def get_icon_path():
    favicon_path = os.path.join(RESOURCE_DIR, "static", "favicon.ico")
    if os.path.exists(favicon_path):
        return favicon_path
    return os.path.join(RESOURCE_DIR, "static", "arcana.jpg")


def open_dashboard():
    webbrowser.open(APP_URL)


def perform_graceful_shutdown():
    app_log("Tray exit requested.")
    request_shutdown()
    stop_library_observer(timeout=SHUTDOWN_JOIN_TIMEOUT_SECONDS)
    shutdown_flask_server()
    join_background_workers(
        {
            "tray-server": flask_thread,
            "library-watchdog": scanner_thread,
            "periodic-sync": sync_thread,
        },
        timeout=SHUTDOWN_JOIN_TIMEOUT_SECONDS,
    )


def run_qt_tray():
    from PySide6.QtCore import QObject, QPoint, Qt, QThread, QTimer, Signal
    from PySide6.QtGui import QColor, QCursor, QIcon, QPixmap
    from PySide6.QtWidgets import (
        QApplication,
        QFrame,
        QGraphicsDropShadowEffect,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QSystemTrayIcon,
        QVBoxLayout,
        QWidget,
    )

    class RefreshWorker(QObject):
        finished = Signal(str)

        def run(self):
            try:
                result = sync_all_library("Tray manual library sync")
                if isinstance(result, dict) and result.get("skipped"):
                    self.finished.emit("Sync already running.")
                    return
                self.finished.emit("Completed")
            except Exception as error:
                app_log(f"Tray refresh failed: {error}", "ERROR")
                self.finished.emit("Refresh failed.")

    class TrayPopup(QFrame):
        open_requested = Signal()
        refresh_requested = Signal()
        exit_requested = Signal()

        def __init__(self, icon_path):
            super().__init__(None)
            self.setWindowFlags(
                Qt.FramelessWindowHint
                | Qt.Tool
                | Qt.WindowStaysOnTopHint
            )
            self.setAttribute(Qt.WA_TranslucentBackground)
            self.setObjectName("TrayPopup")

            self.shell = QFrame(self)
            self.shell.setObjectName("PopupShell")
            shadow = QGraphicsDropShadowEffect(self.shell)
            shadow.setBlurRadius(28)
            shadow.setOffset(0, 10)
            shadow.setColor(QColor(0, 0, 0, 170))
            self.shell.setGraphicsEffect(shadow)

            root = QVBoxLayout(self)
            root.setContentsMargins(18, 18, 18, 18)
            root.addWidget(self.shell)

            layout = QVBoxLayout(self.shell)
            layout.setContentsMargins(18, 18, 18, 18)
            layout.setSpacing(12)

            header = QHBoxLayout()
            header.setSpacing(12)
            logo = QLabel()
            pixmap = QPixmap(icon_path)
            logo.setPixmap(pixmap.scaled(42, 42, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            logo.setFixedSize(42, 42)
            header.addWidget(logo)

            title_box = QVBoxLayout()
            title_box.setSpacing(2)
            title = QLabel("AniBase")
            title.setObjectName("Title")
            self.server_status = QLabel("Server Running")
            self.server_status.setObjectName("Running")
            title_box.addWidget(title)
            title_box.addWidget(self.server_status)
            header.addLayout(title_box, 1)
            layout.addLayout(header)

            self.url_label = QLabel("127.0.0.1:5000")
            self.url_label.setObjectName("Muted")
            layout.addWidget(self.url_label)

            self.lan_status = QLabel("LAN Disabled")
            self.lan_status.setObjectName("StatusPill")
            layout.addWidget(self.lan_status)

            self.refresh_status = QLabel("")
            self.refresh_status.setObjectName("Muted")
            self.refresh_status.setMinimumHeight(18)
            layout.addWidget(self.refresh_status)

            self.open_button = QPushButton("Open Dashboard")
            self.refresh_button = QPushButton("Refresh Library")
            self.exit_button = QPushButton("Exit")
            self.exit_button.setObjectName("ExitButton")
            layout.addWidget(self.open_button)
            layout.addWidget(self.refresh_button)
            layout.addWidget(self.exit_button)

            self.open_button.clicked.connect(self.open_requested)
            self.refresh_button.clicked.connect(self.refresh_requested)
            self.exit_button.clicked.connect(self.exit_requested)

            self.setStyleSheet("""
                QFrame#PopupShell {
                    background: #10131c;
                    border: 1px solid rgba(255, 255, 255, 0.10);
                    border-radius: 16px;
                }
                QLabel {
                    color: #eef3ff;
                    font-family: "Segoe UI";
                    font-size: 12px;
                }
                QLabel#Title {
                    font-size: 16px;
                    font-weight: 700;
                }
                QLabel#Running {
                    color: #74f2a6;
                    font-weight: 600;
                }
                QLabel#Muted {
                    color: #9ba8c7;
                }
                QLabel#StatusPill {
                    background: rgba(87, 114, 255, 0.14);
                    color: #cbd5ff;
                    border: 1px solid rgba(124, 147, 255, 0.28);
                    border-radius: 10px;
                    padding: 8px 10px;
                }
                QPushButton {
                    background: #20283a;
                    color: #f4f7ff;
                    border: 1px solid rgba(255, 255, 255, 0.10);
                    border-radius: 10px;
                    padding: 10px 12px;
                    text-align: left;
                    font-family: "Segoe UI";
                    font-weight: 600;
                }
                QPushButton:hover {
                    background: #2a3650;
                }
                QPushButton:disabled {
                    color: #6f7890;
                    background: #171b26;
                }
                QPushButton#ExitButton {
                    color: #ffd5d8;
                    background: #3a2028;
                }
                QPushButton#ExitButton:hover {
                    background: #4b2631;
                }
            """)
            self.setFixedWidth(300)

        def update_lan_status(self):
            settings = load_settings()
            enabled = bool(settings.get("lan_access_enabled"))
            self.lan_status.setText("LAN Enabled" if enabled else "LAN Disabled")

        def set_refresh_status(self, text):
            self.refresh_status.setText(text)

        def set_refreshing(self, refreshing):
            self.refresh_button.setDisabled(refreshing)
            if refreshing:
                self.set_refresh_status("Refreshing...")

        def event(self, event):
            if event.type() == event.Type.WindowDeactivate:
                self.hide()
            return super().event(event)

    class TrayController(QObject):
        def __init__(self, qt_app):
            super().__init__()
            self.qt_app = qt_app
            self.icon = QIcon(get_icon_path())
            self.tray = QSystemTrayIcon(self.icon, self)
            self.tray.setToolTip("AniBase - Running")
            self.popup = TrayPopup(get_icon_path())
            self.refresh_thread = None
            self.refresh_worker = None
            self.shutting_down = False

            self.popup.open_requested.connect(open_dashboard)
            self.popup.refresh_requested.connect(self.refresh_library)
            self.popup.exit_requested.connect(self.exit_application)
            self.tray.activated.connect(self.handle_tray_activated)

            self.tray.show()

        def handle_tray_activated(self, reason):
            if reason in {
                QSystemTrayIcon.ActivationReason.Trigger,
                QSystemTrayIcon.ActivationReason.DoubleClick,
                QSystemTrayIcon.ActivationReason.Context,
            }:
                self.toggle_popup()

        def toggle_popup(self):
            if self.popup.isVisible():
                self.popup.hide()
                return
            self.show_popup()

        def show_popup(self):
            self.popup.update_lan_status()
            self.popup.adjustSize()
            geometry = self.tray.geometry()
            anchor = geometry.center() if geometry.isValid() else QCursor.pos()
            screen = QApplication.screenAt(anchor) or QApplication.primaryScreen()
            available = screen.availableGeometry()
            size = self.popup.sizeHint()
            x = anchor.x() - size.width() + 24
            y = anchor.y() - size.height() - 12
            x = max(available.left() + 8, min(x, available.right() - size.width() - 8))
            y = max(available.top() + 8, min(y, available.bottom() - size.height() - 8))
            self.popup.move(QPoint(x, y))
            self.popup.show()
            self.popup.raise_()
            self.popup.activateWindow()

        def refresh_library(self):
            if self.refresh_thread and self.refresh_thread.isRunning():
                self.popup.set_refresh_status("Sync already running.")
                return

            self.popup.set_refreshing(True)
            self.refresh_thread = QThread(self)
            self.refresh_worker = RefreshWorker()
            self.refresh_worker.moveToThread(self.refresh_thread)
            self.refresh_thread.started.connect(self.refresh_worker.run)
            self.refresh_worker.finished.connect(self.on_refresh_finished)
            self.refresh_worker.finished.connect(self.refresh_thread.quit)
            self.refresh_worker.finished.connect(self.refresh_worker.deleteLater)
            self.refresh_thread.finished.connect(self.refresh_thread.deleteLater)
            self.refresh_thread.start()

        def on_refresh_finished(self, status):
            self.popup.set_refreshing(False)
            self.popup.set_refresh_status(status)
            self.refresh_thread = None
            self.refresh_worker = None
            QTimer.singleShot(3500, lambda: self.popup.set_refresh_status(""))

        def exit_application(self):
            if self.shutting_down:
                return
            self.shutting_down = True
            self.popup.hide()
            self.tray.hide()
            perform_graceful_shutdown()
            self.qt_app.quit()

    qt_app = QApplication(sys.argv)
    qt_app.setQuitOnLastWindowClosed(False)
    controller = TrayController(qt_app)
    return qt_app.exec()


def main():
    if not acquire_single_instance_guard():
        return 0

    try:
        load_application_components()
        start_background_services()
        return run_qt_tray()
    finally:
        release_instance_mutex()


if __name__ == "__main__":
    raise SystemExit(main())
