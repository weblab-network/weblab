"""Application settings are independent of installed images and lab state."""
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import lab_server


class SettingsTests(unittest.TestCase):
    def test_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            args = lab_server.parse_args([])
        self.assertEqual((args.bind, args.port), ("127.0.0.1", 8080))
        self.assertEqual(args.image_dir, lab_server.ROOT)
        self.assertEqual(args.data_dir, lab_server.ROOT / ".lab")

    def test_environment_and_cli_precedence(self):
        env = dict(WL_BIND="192.0.2.1", WL_PORT="8090",
                   WL_IMAGES_DIR="/tmp/custom-images", WL_DATA_DIR="/tmp/custom-data")
        with patch.dict(os.environ, env, clear=True):
            args = lab_server.parse_args([])
            explicit = lab_server.parse_args([
                "--bind", "127.0.0.1", "--port", "8091",
                "--image-dir", "/tmp/other-images", "--data-dir", "/tmp/other-data"])
        self.assertEqual((args.bind, args.port), ("192.0.2.1", 8090))
        self.assertEqual(args.image_dir, Path(env["WL_IMAGES_DIR"]))
        self.assertEqual(args.data_dir, Path(env["WL_DATA_DIR"]))
        self.assertEqual((explicit.bind, explicit.port), ("127.0.0.1", 8091))
        self.assertEqual(explicit.image_dir, Path("/tmp/other-images"))
        self.assertEqual(explicit.data_dir, Path("/tmp/other-data"))
