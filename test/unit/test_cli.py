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

        # By default, find_* returns None so resources are created
        self.mock_os_cloud.network.find_network.return_value = None
        self.mock_os_cloud.network.find_subnet.return_value = None
        self.mock_os_cloud.compute.find_server_group.return_value = None

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
                "--volume-type=NotDefault",
                "--prefix=unittest",
                "--storage-zone=StorageZone",
            ],
        )
        self.assertEqual(result.exit_code, 0, (result, result.stdout))
        self.assertEqual(mock_add_volume.call_count, 5)
        mock_add_volume.assert_called_with(
            "unittest-0-volume-4",
            "StorageZone",
            999,
            "NotDefault",
            ANY,
            report=ANY,
        )

    def test_cli_8(self):
        result = self.runner.invoke(
            app,
            [
                "--cloud=test",
                "--flavor=testflavor",
                "--image=testimage",
            ],
        )
        self.assertEqual(result.exit_code, 0, (result, result.stdout))
        self.mock_connect.assert_called_with(cloud="test")
        self.mock_os_cloud.get_flavor.assert_called_with("testflavor")
        self.mock_os_cloud.get_image.assert_called_with("testimage")

    def test_cli_9(self):
        result = self.runner.invoke(app, ["--compute-zone=ComputeZone"])
        self.assertEqual(result.exit_code, 0, (result, result.stdout))
        self.mock_os_cloud.compute.create_server.assert_called_with(
            availability_zone="ComputeZone",
            name=ANY,
            flavor_id=ANY,
            networks=ANY,
            user_data=ANY,
            scheduler_hints=ANY,
            block_device_mapping=ANY,
        )

    def test_cli_10(self):
        mock_server_group = MagicMock()
        self.mock_os_cloud.compute.create_server_group.return_value = mock_server_group

        result = self.runner.invoke(app, [])
        self.assertEqual(result.exit_code, 0, (result, result.stdout))
        self.mock_os_cloud.compute.create_server_group.assert_called_with(
            name="simple-stress",
            policies=["soft-anti-affinity"],
        )
        self.mock_os_cloud.compute.delete_server_group.assert_called_with(
            mock_server_group
        )

    def test_cli_11(self):
        mock_server_group = MagicMock()
        self.mock_os_cloud.compute.create_server_group.return_value = mock_server_group

        result = self.runner.invoke(app, ["--affinity=anti-affinity"])
        self.assertEqual(result.exit_code, 0, (result, result.stdout))
        self.mock_os_cloud.compute.create_server_group.assert_called_with(
            name="simple-stress",
            policies=["anti-affinity"],
        )
        self.mock_os_cloud.compute.delete_server_group.assert_called_with(
            mock_server_group
        )

    def test_cli_12(self):
        mock_network = MagicMock()
        self.mock_os_cloud.network.create_network.return_value = mock_network
        mock_network.id = 1234

        mock_subnet = MagicMock()
        self.mock_os_cloud.network.create_subnet.return_value = mock_subnet

        result = self.runner.invoke(app, ["--subnet-cidr=10.100.1.0/24"])
        self.assertEqual(result.exit_code, 0, (result, result.stdout))
        self.mock_os_cloud.network.create_network.assert_called_once_with(
            name="simple-stress",
        )
        self.mock_os_cloud.network.create_subnet.assert_called_once_with(
            name="simple-stress-subnet",
            network_id=1234,
            ip_version="4",
            cidr="10.100.1.0/24",
        )
        self.mock_os_cloud.network.delete_subnet.assert_called_with(
            mock_subnet, ignore_missing=False
        )
        self.mock_os_cloud.network.delete_network.assert_called_with(
            mock_network, ignore_missing=False
        )

    def test_cli_13(self):
        # Test fallback for invalid cidr input
        result = self.runner.invoke(app, ["--subnet-cidr=10.100.255.255/24"])
        self.assertEqual(result.exit_code, 0, (result, result.stdout))

        self.mock_os_cloud.network.create_subnet.assert_called_once_with(
            name=ANY,
            network_id=ANY,
            ip_version=ANY,
            cidr="10.100.0.0/16",
        )

    def test_default_mode_is_rolling(self):
        result = self.runner.invoke(app, ["--help"])
        self.assertEqual(result.exit_code, 0, (result, result.stdout))
        self.assertIn("rolling", result.stdout)

    @patch("openstack_simple_stress.main.create")
    def test_mode_rolling_explicit(self, mock_create):
        result = self.runner.invoke(app, ["--mode=rolling", "--number=3"])
        self.assertEqual(result.exit_code, 0, (result, result.stdout))
        self.assertEqual(mock_create.call_count, 3)

    @patch("openstack_simple_stress.main.create")
    def test_mode_block(self, mock_create):
        result = self.runner.invoke(app, ["--mode=block", "--number=4", "--parallel=2"])
        self.assertEqual(result.exit_code, 0, (result, result.stdout))
        self.assertEqual(mock_create.call_count, 4)

    @patch("openstack_simple_stress.main.create")
    def test_mode_block_uneven(self, mock_create):
        result = self.runner.invoke(app, ["--mode=block", "--number=5", "--parallel=2"])
        self.assertEqual(result.exit_code, 0, (result, result.stdout))
        self.assertEqual(mock_create.call_count, 5)

    def test_mode_invalid(self):
        result = self.runner.invoke(app, ["--mode=invalid"])
        self.assertNotEqual(result.exit_code, 0)

    def test_clean_no_resources(self):
        self.mock_os_cloud.compute.servers.return_value = []
        self.mock_os_cloud.block_storage.volumes.return_value = []
        self.mock_os_cloud.compute.find_server_group.return_value = None
        self.mock_os_cloud.network.find_subnet.return_value = None
        self.mock_os_cloud.network.find_network.return_value = None

        result = self.runner.invoke(app, ["--clean"])
        self.assertEqual(result.exit_code, 0, (result, result.stdout))
        # Should not attempt any deletions
        self.mock_os_cloud.compute.delete_server.assert_not_called()
        # Should not validate flavor/image
        self.mock_os_cloud.get_flavor.assert_not_called()
        self.mock_os_cloud.get_image.assert_not_called()

    def test_clean_with_resources_confirmed(self):
        mock_server = MagicMock()
        mock_server.name = "simple-stress-0"
        mock_server.id = "srv-123"
        mock_server.status = "ACTIVE"
        self.mock_os_cloud.compute.servers.return_value = [mock_server]

        mock_volume = MagicMock()
        mock_volume.name = "simple-stress-0-volume-0"
        mock_volume.id = "vol-456"
        mock_volume.status = "in-use"
        self.mock_os_cloud.block_storage.volumes.return_value = [mock_volume]

        mock_server_group = MagicMock()
        mock_server_group.name = "simple-stress"
        mock_server_group.id = "sg-789"
        self.mock_os_cloud.compute.find_server_group.return_value = mock_server_group

        mock_subnet = MagicMock()
        mock_subnet.name = "simple-stress-subnet"
        mock_subnet.id = "sub-abc"
        self.mock_os_cloud.network.find_subnet.return_value = mock_subnet

        mock_network = MagicMock()
        mock_network.name = "simple-stress"
        mock_network.id = "net-def"
        self.mock_os_cloud.network.find_network.return_value = mock_network

        result = self.runner.invoke(app, ["--clean"], input="y\n")
        self.assertEqual(result.exit_code, 0, (result, result.stdout))
        self.mock_os_cloud.compute.delete_server.assert_called_once_with(mock_server)
        self.mock_os_cloud.compute.wait_for_delete.assert_called_once_with(mock_server)
        self.mock_os_cloud.block_storage.delete_volume.assert_called_once_with(
            mock_volume
        )
        self.mock_os_cloud.block_storage.wait_for_delete.assert_called_once_with(
            mock_volume
        )
        self.mock_os_cloud.compute.delete_server_group.assert_called_once_with(
            mock_server_group
        )
        self.mock_os_cloud.network.delete_subnet.assert_called_once_with(
            mock_subnet, ignore_missing=False
        )
        self.mock_os_cloud.network.delete_network.assert_called_once_with(
            mock_network, ignore_missing=False
        )

    def test_clean_with_resources_denied(self):
        mock_server = MagicMock()
        mock_server.name = "simple-stress-0"
        mock_server.id = "srv-123"
        mock_server.status = "ACTIVE"
        self.mock_os_cloud.compute.servers.return_value = [mock_server]

        self.mock_os_cloud.block_storage.volumes.return_value = []
        self.mock_os_cloud.compute.find_server_group.return_value = None
        self.mock_os_cloud.network.find_subnet.return_value = None
        self.mock_os_cloud.network.find_network.return_value = None

        result = self.runner.invoke(app, ["--clean"], input="n\n")
        self.assertEqual(result.exit_code, 0, (result, result.stdout))
        # Should not delete anything
        self.mock_os_cloud.compute.delete_server.assert_not_called()

    def test_clean_with_custom_prefix(self):
        self.mock_os_cloud.compute.servers.return_value = []
        self.mock_os_cloud.block_storage.volumes.return_value = []
        self.mock_os_cloud.compute.find_server_group.return_value = None
        self.mock_os_cloud.network.find_subnet.return_value = None
        self.mock_os_cloud.network.find_network.return_value = None

        result = self.runner.invoke(app, ["--clean", "--prefix=mytest"])
        self.assertEqual(result.exit_code, 0, (result, result.stdout))
        self.mock_os_cloud.compute.servers.assert_called_once_with(name="^mytest-")
        self.mock_os_cloud.compute.find_server_group.assert_called_with("mytest")
        self.mock_os_cloud.network.find_subnet.assert_called_with("mytest-subnet")
        self.mock_os_cloud.network.find_network.assert_called_with("mytest")

    def test_clean_skips_non_matching_volumes(self):
        mock_volume_match = MagicMock()
        mock_volume_match.name = "simple-stress-0-volume-0"
        mock_volume_match.id = "vol-match"
        mock_volume_match.status = "available"

        mock_volume_other = MagicMock()
        mock_volume_other.name = "other-project-volume"
        mock_volume_other.id = "vol-other"
        mock_volume_other.status = "available"

        self.mock_os_cloud.compute.servers.return_value = []
        self.mock_os_cloud.block_storage.volumes.return_value = [
            mock_volume_match,
            mock_volume_other,
        ]
        self.mock_os_cloud.compute.find_server_group.return_value = None
        self.mock_os_cloud.network.find_subnet.return_value = None
        self.mock_os_cloud.network.find_network.return_value = None

        result = self.runner.invoke(app, ["--clean"], input="y\n")
        self.assertEqual(result.exit_code, 0, (result, result.stdout))
        # Only matching volume should be deleted
        self.mock_os_cloud.block_storage.delete_volume.assert_called_once_with(
            mock_volume_match
        )


if __name__ == "__main__":
    unittest.main()
