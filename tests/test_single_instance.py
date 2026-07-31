from __future__ import annotations

import quicktype.single_instance as single_instance_module
from quicktype.single_instance import SingleInstance


class _Signal:
    def connect(self, _callback) -> None:
        pass


class _Server:
    def __init__(self, _parent) -> None:
        self.newConnection = _Signal()
        self.listen_attempts = 0

    def listen(self, _name: str) -> bool:
        self.listen_attempts += 1
        return False

    def errorString(self) -> str:
        return "endpoint unavailable"


def test_listen_does_not_remove_a_live_server(monkeypatch) -> None:
    server = _Server(None)
    removed: list[str] = []
    monkeypatch.setattr(
        single_instance_module,
        "QLocalServer",
        lambda _parent: server,
    )
    monkeypatch.setattr(
        SingleInstance,
        "notify_existing",
        staticmethod(lambda: True),
    )
    monkeypatch.setattr(
        single_instance_module.QLocalServer,
        "removeServer",
        lambda name: removed.append(name),
        raising=False,
    )

    instance = SingleInstance(lambda: None)

    assert not instance.listen()
    assert server.listen_attempts == 1
    assert removed == []
