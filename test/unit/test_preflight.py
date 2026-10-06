import unittest
from unittest.mock import MagicMock, patch

import typer
from typer.testing import CliRunner

from openstack_simple_stress.main import Cloud, PreflightError, preflight, run

app = typer.Typer()
app.command()(run)


def _cloud(flavor_disk=10, min_disk=8, has_block_storage=True):
    cloud = MagicMock()
    cloud.os_flavor = MagicMock(disk=flavor_disk)
    cloud.os_flavor.name = "flv"
    cloud.os_image = MagicMock(min_disk=min_disk)
    cloud.os_image.name = "img"
    cloud.os_cloud.has_service.return_value = has_block_storage
    return cloud


class TestPreflight(unittest.TestCase):

    def test_local_boot_zero_disk_flavor_fails(self):
        with self.assertRaisesRegex(PreflightError, "no root disk"):
            preflight(_cloud(flavor_disk=0), boot_from_volume=False, volumes=False)

    def test_local_boot_flavor_smaller_than_min_disk_fails(self):
        with self.assertRaisesRegex(PreflightError, "smaller than"):
            preflight(
                _cloud(flavor_disk=5, min_disk=8), boot_from_volume=False, volumes=False
            )

    def test_local_boot_image_without_min_disk_passes(self):
        preflight(
            _cloud(flavor_disk=5, min_disk=None), boot_from_volume=False, volumes=False
        )

    def test_boot_from_volume_ignores_flavor_disk(self):
        preflight(_cloud(flavor_disk=0), boot_from_volume=True, volumes=False)

    def test_volumes_without_block_storage_fail(self):
        with self.assertRaisesRegex(PreflightError, "--no-volume --no-boot-volume"):
            preflight(
                _cloud(has_block_storage=False), boot_from_volume=False, volumes=True
            )

    def test_boot_from_volume_without_block_storage_fails(self):
        with self.assertRaisesRegex(PreflightError, "block storage"):
            preflight(
                _cloud(has_block_storage=False), boot_from_volume=True, volumes=False
            )

    def test_no_volumes_need_no_block_storage(self):
        preflight(
            _cloud(has_block_storage=False), boot_from_volume=False, volumes=False
        )


class TestCloudLookup(unittest.TestCase):

    @patch("openstack.connect")
    def test_missing_flavor_raises(self, mock_connect):
        mock_connect.return_value.get_flavor.return_value = None
        with self.assertRaisesRegex(PreflightError, "Flavor 'nope' not found"):
            Cloud("c", "nope", "img")

    @patch("openstack.connect")
    def test_missing_image_raises(self, mock_connect):
        mock_connect.return_value.get_image.return_value = None
        with self.assertRaisesRegex(PreflightError, "Image 'nope' not found"):
            Cloud("c", "flv", "nope")


class TestPreflightCLI(unittest.TestCase):

    def setUp(self):
        patcher = patch("openstack.connect")
        self.mock_connect = patcher.start()
        self.addCleanup(patcher.stop)
        self.os_cloud = MagicMock()
        self.mock_connect.return_value = self.os_cloud
        self.os_cloud.network.find_network.return_value = None
        self.os_cloud.network.find_subnet.return_value = None
        self.os_cloud.compute.find_server_group.return_value = None
        self.runner = CliRunner()

    def test_failed_preflight_exits_2_and_creates_nothing(self):
        self.os_cloud.has_service.return_value = False

        result = self.runner.invoke(app, [])
        self.assertEqual(result.exit_code, 2, (result, result.stdout))
        self.os_cloud.network.create_network.assert_not_called()
        self.os_cloud.compute.create_server.assert_not_called()

    def test_missing_image_exits_2(self):
        self.os_cloud.get_image.return_value = None

        result = self.runner.invoke(app, [])
        self.assertEqual(result.exit_code, 2, (result, result.stdout))
        self.os_cloud.network.create_network.assert_not_called()
