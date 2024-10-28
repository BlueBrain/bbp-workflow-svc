"""Main module tests."""

import sh

from bbp_workflow_svc.main import _dump_files, _ssh_agt, _zip_files


def test_post_files_payload_storage(tmp_path):
    """."""
    files = {
        "test.py": [
            {"body": b"dummy", "content_type": "application/octet-stream", "filename": "test.py"}
        ]
    }
    buf, _ = _zip_files(files, None)
    _dump_files(buf, tmp_path)
    assert (tmp_path / "test.py").exists()


def test_ssh_agt(monkeypatch):
    """Test ssh agent."""

    class DummySshAgent:  # noqa
        # pylint: disable=missing-class-docstring,missing-function-docstring
        def __init__(self):
            self._stop = False

        def __iter__(self):
            return self

        def __next__(self):
            if not self._stop:
                self._stop = True
                return "SSH_AUTH_SOCK=/tmp/ssh-XXXXXX/agent.123; export SSH_AUTH_SOCK;"
            else:
                raise StopIteration

        def is_alive(self):
            return True

        def terminate(self):
            return True

    monkeypatch.setattr(sh, "ssh_agent", lambda *_, **__: DummySshAgent())
    monkeypatch.setattr(sh, "ssh_add", lambda *_, **__: None)

    with _ssh_agt(key="dummy") as ssh_auth_sock:
        assert ssh_auth_sock["SSH_AUTH_SOCK"] == "/tmp/ssh-XXXXXX/agent.123"
