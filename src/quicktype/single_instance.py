from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from .constants import SINGLE_INSTANCE_NAME


class SingleInstance(QObject):
    def __init__(self, on_activate: Callable[[], None]) -> None:
        super().__init__()
        self._on_activate = on_activate
        self._server: QLocalServer | None = None

    @staticmethod
    def notify_existing() -> bool:
        socket = QLocalSocket()
        socket.connectToServer(SINGLE_INSTANCE_NAME)
        if not socket.waitForConnected(250):
            return False
        socket.write(b"show")
        socket.flush()
        socket.waitForBytesWritten(250)
        socket.disconnectFromServer()
        return True

    def listen(self) -> None:
        server = QLocalServer(self)
        if not server.listen(SINGLE_INSTANCE_NAME):
            QLocalServer.removeServer(SINGLE_INSTANCE_NAME)
            if not server.listen(SINGLE_INSTANCE_NAME):
                raise RuntimeError(server.errorString())
        server.newConnection.connect(self._handle_connection)
        self._server = server

    def _handle_connection(self) -> None:
        if self._server is None:
            return
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            if socket is not None:
                socket.disconnectFromServer()
                socket.deleteLater()
            self._on_activate()
