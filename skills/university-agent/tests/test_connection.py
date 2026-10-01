"""Account connection opens a native terminal, not a credential-capturing host PTY."""
import unittest
from unittest.mock import patch
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import sync_tls
from features.guidance import guidance_request


class ConnectionTests(unittest.TestCase):
    def test_connection_action_and_mac_terminal_command(self):
        self.assertEqual(guidance_request('TLS 연결해줘')['nextCommands'],
                         ['python3 scripts/sync_tls.py --connect'])
        with patch.object(sync_tls.sys, 'platform', 'darwin'), patch.object(sync_tls.subprocess, 'run') as run:
            sync_tls.open_connection_terminal()
        args = run.call_args.args[0]
        self.assertEqual(args[0], 'osascript')
        self.assertIn('sync_tls.py', ' '.join(args))
        self.assertNotIn('--connect', ' '.join(args))

    def test_windows_uses_new_console(self):
        with patch.object(sync_tls.sys, 'platform', 'win32'), patch.object(sync_tls.os, 'name', 'nt'), patch.object(sync_tls.subprocess, 'CREATE_NEW_CONSOLE', 16, create=True), patch.object(sync_tls.subprocess, 'Popen') as launch:
            # Avoid selecting WindowsPath on this Mac during the simulated launch.
            with patch.object(sync_tls, 'Path') as path:
                path.return_value.resolve.return_value = '/fixture/sync_tls.py'
                sync_tls.open_connection_terminal()
        self.assertEqual(launch.call_args.kwargs['creationflags'], 16)
        self.assertNotIn('--connect', launch.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
