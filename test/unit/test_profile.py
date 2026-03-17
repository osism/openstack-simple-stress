import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import typer
from typer.testing import CliRunner

from openstack_simple_stress.main import (
    load_profile,
    run,
    VALID_PROFILE_KEYS,
)


app = typer.Typer()
app.command()(run)


class TestLoadProfile(unittest.TestCase):

    def test_load_profile_from_absolute_path(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("number: 42\nparallel: 8\n")
            f.flush()
            try:
                data = load_profile(f.name)
                self.assertEqual(data["number"], 42)
                self.assertEqual(data["parallel"], 8)
            finally:
                os.unlink(f.name)

    def test_load_builtin_profile_by_name(self):
        data = load_profile("quick")
        self.assertIn("number", data)
        self.assertIn("parallel", data)

    def test_load_builtin_profile_with_extension(self):
        data = load_profile("quick.yaml")
        self.assertIn("number", data)

    def test_load_profile_not_found(self):
        with self.assertRaises(SystemExit):
            load_profile("nonexistent_profile_xyz")

    def test_load_profile_empty_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("")
            f.flush()
            try:
                data = load_profile(f.name)
                self.assertEqual(data, {})
            finally:
                os.unlink(f.name)

    def test_load_profile_invalid_format(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("- item1\n- item2\n")
            f.flush()
            try:
                with self.assertRaises(SystemExit):
                    load_profile(f.name)
            finally:
                os.unlink(f.name)

    def test_load_profile_unknown_keys_does_not_fail(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("number: 5\nunknown_key: value\n")
            f.flush()
            try:
                data = load_profile(f.name)
                self.assertEqual(data["number"], 5)
                self.assertIn("unknown_key", data)
            finally:
                os.unlink(f.name)

    def test_all_builtin_profiles_are_valid(self):
        for name in ["quick", "stress", "volume", "persistent"]:
            data = load_profile(name)
            self.assertIsInstance(data, dict)
            unknown = set(data.keys()) - VALID_PROFILE_KEYS
            self.assertEqual(
                unknown, set(), f"Profile '{name}' has unknown keys: {unknown}"
            )


class TestCLIWithProfile(unittest.TestCase):

    def setUp(self):
        self.patcher = patch("openstack.connect")
        self.mock_connect = self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.mock_os_cloud = MagicMock()
        self.mock_connect.return_value = self.mock_os_cloud
        self.mock_os_cloud.compute.get_server_console_output.return_value = (
            "The system is finally up"
        )
        self.mock_os_cloud.network.find_network.return_value = None
        self.mock_os_cloud.network.find_subnet.return_value = None
        self.mock_os_cloud.compute.find_server_group.return_value = None

        self.runner = CliRunner()

    def _write_profile(self, content):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write(content)
        f.flush()
        f.close()
        self.addCleanup(lambda: os.unlink(f.name))
        return f.name

    @patch("openstack_simple_stress.main.create")
    def test_profile_overrides_defaults(self, mock_create):
        path = self._write_profile("number: 7\nparallel: 3\n")

        result = self.runner.invoke(app, [f"--profile={path}"])
        self.assertEqual(result.exit_code, 0, (result, result.stdout))
        self.assertEqual(mock_create.call_count, 7)

    @patch("openstack_simple_stress.main.create")
    def test_cli_overrides_profile(self, mock_create):
        path = self._write_profile("number: 7\n")

        result = self.runner.invoke(app, [f"--profile={path}", "--number=2"])
        self.assertEqual(result.exit_code, 0, (result, result.stdout))
        self.assertEqual(mock_create.call_count, 2)

    def test_profile_sets_cloud(self):
        path = self._write_profile("cloud: mycloud\n")

        result = self.runner.invoke(app, [f"--profile={path}"])
        self.assertEqual(result.exit_code, 0, (result, result.stdout))
        self.mock_connect.assert_called_with(cloud="mycloud")

    def test_cli_cloud_overrides_profile_cloud(self):
        path = self._write_profile("cloud: profilecloud\n")

        result = self.runner.invoke(app, [f"--profile={path}", "--cloud=clicloud"])
        self.assertEqual(result.exit_code, 0, (result, result.stdout))
        self.mock_connect.assert_called_with(cloud="clicloud")

    def test_profile_sets_flavor_and_image(self):
        path = self._write_profile("flavor: myflavor\nimage: myimage\n")

        result = self.runner.invoke(app, [f"--profile={path}"])
        self.assertEqual(result.exit_code, 0, (result, result.stdout))
        self.mock_os_cloud.get_flavor.assert_called_with("myflavor")
        self.mock_os_cloud.get_image.assert_called_with("myimage")

    @patch("openstack_simple_stress.main.create")
    def test_profile_sets_mode_block(self, mock_create):
        path = self._write_profile("number: 4\nparallel: 2\nmode: block\n")

        result = self.runner.invoke(app, [f"--profile={path}"])
        self.assertEqual(result.exit_code, 0, (result, result.stdout))
        self.assertEqual(mock_create.call_count, 4)

    @patch("openstack_simple_stress.main.create")
    def test_profile_sets_affinity(self, mock_create):
        path = self._write_profile("affinity: anti-affinity\n")

        result = self.runner.invoke(app, [f"--profile={path}"])
        self.assertEqual(result.exit_code, 0, (result, result.stdout))
        self.mock_os_cloud.compute.create_server_group.assert_called_with(
            name="simple-stress",
            policies=["anti-affinity"],
        )

    @patch("openstack_simple_stress.main.delete_server")
    def test_profile_sets_no_delete(self, mock_delete_server):
        path = self._write_profile("no_delete: true\nno_cleanup: true\n")

        result = self.runner.invoke(app, [f"--profile={path}"])
        self.assertEqual(result.exit_code, 0, (result, result.stdout))
        mock_delete_server.assert_not_called()

    @patch("openstack_simple_stress.main.Instance.add_volume")
    def test_profile_sets_volume_params(self, mock_add_volume):
        path = self._write_profile("volume: true\nvolume_number: 3\nvolume_size: 50\n")

        result = self.runner.invoke(app, [f"--profile={path}"])
        self.assertEqual(result.exit_code, 0, (result, result.stdout))
        self.assertEqual(mock_add_volume.call_count, 3)

    def test_profile_builtin_quick(self):
        result = self.runner.invoke(app, ["--profile=quick"])
        self.assertEqual(result.exit_code, 0, (result, result.stdout))

    def test_profile_not_found_exits(self):
        result = self.runner.invoke(app, ["--profile=nonexistent_xyz"])
        self.assertNotEqual(result.exit_code, 0)

    def test_no_profile_still_works(self):
        result = self.runner.invoke(app, [])
        self.assertEqual(result.exit_code, 0, (result, result.stdout))


if __name__ == "__main__":
    unittest.main()
