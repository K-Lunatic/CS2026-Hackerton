import io
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen
from wsgiref.simple_server import make_server

from server.app import Gateway
from server.local import LocalServer, QuietHandler, local_config, start_tunnel, write_settings


class LocalTests(unittest.TestCase):
    def test_config_and_settings_are_private_and_secret_is_reused(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            config = local_config(directory, 'g-test')
            again = local_config(directory, 'g-other')
            self.assertEqual(config['client_secret'], again['client_secret'])
            self.assertEqual(again['gpt_id'], 'g-other')
            app = Gateway('https://example.com', config['client_id'], config['client_secret'], ['https://chatgpt.com/aip/g-test/oauth/callback'], directory / 'data')
            settings = write_settings(directory, app)
            self.assertEqual(settings.stat().st_mode & 0o777, 0o600)
            self.assertEqual((directory / 'config.json').stat().st_mode & 0o777, 0o600)
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
            self.assertIn(config['client_secret'], settings.read_text())

    def test_tunnel_url_and_failure_cleanup(self):
        with patch('server.local.subprocess.Popen') as spawn:
            spawn.return_value.stdout = io.StringIO('ready https://quiet-test.trycloudflare.com\n')
            process, url = start_tunnel('cloudflared', 8766)
            self.assertEqual(url, 'https://quiet-test.trycloudflare.com')
            self.assertEqual(spawn.call_args.args[0][3], 'http://127.0.0.1:8766')
            process.terminate.assert_not_called()
        with patch('server.local.subprocess.Popen') as spawn:
            spawn.return_value.stdout = io.StringIO('failed\n')
            with self.assertRaises(RuntimeError):
                start_tunnel('cloudflared', 8766)
            spawn.return_value.terminate.assert_called_once()

    def test_local_http_health_and_schema_do_not_expose_secret(self):
        with tempfile.TemporaryDirectory() as folder:
            app = Gateway('https://example.com', 'client', 'fixture-secret-' * 4, ['https://chatgpt.com/aip/g-test/oauth/callback'], folder)
            server = make_server('127.0.0.1', 0, app, server_class=LocalServer, handler_class=QuietHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                for path in ('health', 'openapi.json'):
                    with urlopen(f'http://127.0.0.1:{server.server_port}/{path}', timeout=5) as response:
                        self.assertEqual(response.status, 200)
                        self.assertNotIn(app.client_secret, response.read().decode())
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == '__main__':
    unittest.main()
