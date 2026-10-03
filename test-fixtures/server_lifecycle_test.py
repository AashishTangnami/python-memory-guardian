"""Debounced diagnostics must not outlive their documents."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import guardian_server as server


class ServerLifecycle(unittest.TestCase):
    def test_close_cancels_pending_work(self):
        uri = 'file:///review.py'
        handle = Mock()
        server._pending[uri] = handle
        ls = Mock()
        server.did_close(ls, SimpleNamespace(text_document=SimpleNamespace(uri=uri)))
        handle.cancel.assert_called_once()
        self.assertNotIn(uri, server._pending)
        self.assertEqual(ls.text_document_publish_diagnostics.call_args.args[0].diagnostics, [])

    def test_explicit_empty_probe_stays_neutral(self):
        with patch.object(server.probe, 'probe') as probe:
            server.on_initialize(Mock(), SimpleNamespace(initialization_options={'profile': {}}))
            probe.assert_not_called()
            self.assertEqual(server.FACTS, {})


if __name__ == '__main__':
    unittest.main()
