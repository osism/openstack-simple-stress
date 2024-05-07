import unittest
from unittest.mock import MagicMock, patch
from unittest.mock import ANY

import typer
from typer.testing import CliRunner

from openstack_simple_stress.main import (
    run,
)


app = typer.Typer()
app.command()(run)


class TestCLI(unittest.TestCase):

    def setUp(self):
        self.patcher = patch("openstack.connect")
        self.mock_connect = self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.mock_os_cloud = MagicMock()
        self.mock_connect.return_value = self.mock_os_cloud
        self.mock_os_cloud.compute.get_server_console_output.return_value = (
            "The system is finally up"
        )

        self.runner = CliRunner()

    def test_cli_0(self):
        result = self.runner.invoke(app, ["--help"])
        self.assertEqual(result.exit_code, 0, (result, result.stdout))

    def test_cli_1(self):
        result = self.runner.invoke(app, ["--debug"])
        self.assertEqual(result.exit_code, 0, (result, result.stdout))

    @patch("openstack_simple_stress.main.delete_server")
    def test_cli_2(self, mock_delete_server):
        result = self.runner.invoke(app, ["--no-cleanup", "--no-delete"])
        self.assertEqual(result.exit_code, 0, (result, result.stdout))
        mock_delete_server.assert_not_called()

    @patch("openstack_simple_stress.main.Instance.add_volume")
    def test_cli_3(self, mock_add_volume):
        result = self.runner.invoke(app, ["--volume"])
        self.assertEqual(result.exit_code, 0, (result, result.stdout))
        mock_add_volume.assert_called_once()

    def test_cli_4(self):
        result = self.runner.invoke(app, ["--no-wait"])
        self.mock_os_cloud.compute.get_server_console_output.return_value = "hang"
        self.assertEqual(result.exit_code, 0, (result, result.stdout))

    def test_cli_5(self):
        mock_server = MagicMock()
        self.mock_os_cloud.compute.create_server.return_value = mock_server
        result = self.runner.invoke(app, ["--interval=200", "--timeout=999"])
        self.assertEqual(result.exit_code, 0, (result, result.stdout))
        self.mock_os_cloud.compute.wait_for_server.assert_called_with(
            mock_server,
            interval=200,
            wait=999,
        )

    @patch("openstack_simple_stress.main.create")
    def test_cli_6(self, mock_create):
        result = self.runner.invoke(app, ["--number=6", "--parallel=2"])
        self.assertEqual(result.exit_code, 0, (result, result.stdout))
        self.assertEqual(mock_create.call_count, 6)

    @patch("openstack_simple_stress.main.Instance.add_volume")
    def test_cli_7(self, mock_add_volume):
        result = self.runner.invoke(
            app,
            [
                "--volume",
                "--volume-number=5",
                "--volume-size=999",
                "--prefix=unittest",
                "--storage-zone=StorageZone",
            ],
        )
        self.assertEqual(result.exit_code, 0, (result, result.stdout))
        self.assertEqual(mock_add_volume.call_count, 5)
        mock_add_volume.assert_called_with(
            "unittest-0-volume-4", "StorageZone", 999, ANY
        )

    def test_cli_8(self):
        result = self.runner.invoke(
            app,
            [
                "--cloud=test",
                "--flavor=testflavor",
                "--image=testimage",
                "--network=testnetwork",
            ],
        )
        self.assertEqual(result.exit_code, 0, (result, result.stdout))
        self.mock_connect.assert_called_with(cloud="test")
        self.mock_os_cloud.get_flavor.assert_called_with("testflavor")
        self.mock_os_cloud.get_image.assert_called_with("testimage")
        self.mock_os_cloud.get_network.assert_called_with("testnetwork")

    def test_cli_9(self):
        result = self.runner.invoke(app, ["--compute-zone=ComputeZone"])
        self.assertEqual(result.exit_code, 0, (result, result.stdout))
        self.mock_os_cloud.compute.create_server.assert_called_with(
            availability_zone="ComputeZone",
            name=ANY,
            image_id=ANY,
            flavor_id=ANY,
            networks=ANY,
            user_data=ANY,
        )


if __name__ == "__main__":
    unittest.main()
