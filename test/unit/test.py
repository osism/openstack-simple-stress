import unittest
from unittest.mock import MagicMock, patch

from openstack_simple_stress.main import (
    Meta,
    Cloud,
    Instance,
    create,
    create_volume,
    create_server,
    delete_server,
)

MOCK_META = Meta(wait=True, interval=10, timeout=20, delete=False)
MOCK_META_2 = Meta(wait=False, interval=10, timeout=20, delete=True)


class MockVolume:
    def __init__(self, id):
        self.id = id


class MockServer:
    def __init__(self, id):
        self.id = id


class TestBase(unittest.TestCase):

    @patch("openstack.connect")
    def setUp(self, mock_connect):
        mock_os_cloud = MagicMock()
        mock_connect.return_value = mock_os_cloud

        self.mock_cloud = Cloud("CloudName", "FlavorName", "ImageName")

        mock_connect.assert_called_with(cloud="CloudName")
        mock_os_cloud.get_flavor.assert_called_with("FlavorName")
        mock_os_cloud.get_image.assert_called_with("ImageName")


class TestInstance(TestBase):

    @patch("openstack_simple_stress.main.create_server")
    def test_instance_init_0(self, mock_create_server):
        mock_create_server.return_value = MockServer(7)

        instance = Instance(
            self.mock_cloud,
            "ServerName",
            "UserData",
            "ComputeZone",
            MagicMock(),
            MagicMock(),
            MOCK_META,
        )

        self.assertEqual(instance.cloud, self.mock_cloud)
        self.assertEqual(instance.server.id, 7)
        self.assertEqual(instance.server_name, "ServerName")
        self.assertIsInstance(instance.volumes, list)

    @patch("openstack_simple_stress.main.create_volume")
    @patch("openstack_simple_stress.main.create_server")
    def test_instance_add_volume_0(self, mock_create_server, mock_create_volume):
        mock_create_server.return_value = MockServer(7)
        mock_create_volume.return_value = MockVolume(17)

        instance = Instance(
            self.mock_cloud,
            "ServerName",
            "UserData",
            "ComputeZone",
            MagicMock(),
            MagicMock(),
            MOCK_META,
        )

        self.assertEqual(len(instance.volumes), 0)

        instance.add_volume("VolumeName", "StorageZone", 42, "VolumeType", MOCK_META)

        mock_create_volume.assert_called_with(
            self.mock_cloud, "VolumeName", "StorageZone", 42, "VolumeType", MOCK_META
        )
        self.assertEqual(len(instance.volumes), 1)
        self.assertEqual(instance.volumes[0].id, 17)

        instance.add_volume("VolumeName2", "StorageZone2", 23, "VolumeType2", MOCK_META)

        mock_create_volume.assert_called_with(
            self.mock_cloud, "VolumeName2", "StorageZone2", 23, "VolumeType2", MOCK_META
        )
        self.assertEqual(len(instance.volumes), 2)

    @patch("openstack_simple_stress.main.create_volume")
    @patch("openstack_simple_stress.main.create_server")
    def test_instance_attach_volumes_0(self, mock_create_server, mock_create_volume):
        mock_create_server.return_value = MockServer(7)
        mock_create_volume.return_value = MockVolume(17)
        self.mock_cloud.os_cloud.compute.get_server.return_value = MockServer(8)

        instance = Instance(
            self.mock_cloud,
            "ServerName",
            "UserData",
            "ComputeZone",
            MagicMock(),
            MagicMock(),
            MOCK_META,
        )
        instance.add_volume("VolumeName", "StorageZone", 42, "VolumeType", MOCK_META)
        instance.add_volume("VolumeName2", "StorageZone2", 23, "VolumeType2", MOCK_META)

        instance.attach_volumes()

        self.assertEqual(self.mock_cloud.os_cloud.attach_volume.call_count, 2)
        self.mock_cloud.os_cloud.attach_volume.assert_called_with(
            instance.server, mock_create_volume.return_value
        )
        self.assertEqual(instance.server.id, 8)


class TestCreate(TestBase):

    @patch("openstack_simple_stress.main.delete_server")
    @patch("openstack_simple_stress.main.create_volume")
    @patch("openstack_simple_stress.main.create_server")
    def test_create_0(self, mock_create_server, mock_create_volume, mock_delete_server):
        mock_create_server.return_value = MockServer(7)
        mock_create_volume.return_value = MockVolume(17)

        instance = create(
            self.mock_cloud,
            "ServerName",
            "UserData",
            "ComputeZone",
            True,
            5,
            "StorageZone",
            50,
            MagicMock(),
            "VolumeType",
            MagicMock(),
            MOCK_META,
        )

        self.assertEqual(instance.cloud, self.mock_cloud)
        self.assertEqual(instance.server_name, "ServerName")
        self.assertEqual(len(instance.volumes), 5)
        self.assertEqual(mock_delete_server.call_count, 0)

    @patch("openstack_simple_stress.main.delete_server")
    @patch("openstack_simple_stress.main.create_volume")
    @patch("openstack_simple_stress.main.create_server")
    def test_create_1(self, mock_create_server, mock_create_volume, mock_delete_server):
        mock_create_server.return_value = MockServer(7)
        mock_create_volume.return_value = MockVolume(17)

        instance = create(
            self.mock_cloud,
            "ServerName",
            "UserData",
            "ComputeZone",
            True,
            5,
            "StorageZone",
            50,
            MagicMock(),
            "VolumeType",
            MagicMock(),
            MOCK_META_2,
        )

        self.assertEqual(instance.cloud, self.mock_cloud)
        self.assertEqual(instance.server_name, "ServerName")
        self.assertEqual(len(instance.volumes), 5)
        self.assertEqual(mock_delete_server.call_count, 1)

    def test_create_volume_0(self):
        self.mock_cloud.os_cloud.block_storage.create_volume.return_value = MockVolume(
            17
        )

        volume = create_volume(
            self.mock_cloud, "VolumeName", "StorageZone", 22, "VolumeType", MOCK_META
        )

        self.assertEqual(volume.id, 17)
        self.mock_cloud.os_cloud.block_storage.create_volume.assert_called_with(
            availability_zone="StorageZone",
            name="VolumeName",
            size=22,
            volume_type="VolumeType",
        )
        self.mock_cloud.os_cloud.block_storage.wait_for_status.assert_called_with(
            volume,
            status="available",
            interval=MOCK_META.interval,
            wait=MOCK_META.timeout,
        )

    def test_create_server_0(self):
        self.mock_cloud.os_cloud.compute.create_server.return_value = MockServer(7)
        self.mock_cloud.os_cloud.compute.get_server_console_output.return_value = (
            "The system is finally up"
        )
        mock_server_group = MagicMock()
        mock_server_group.id = 1234
        mock_network = MagicMock()
        mock_network.id = 5678

        server = create_server(
            self.mock_cloud,
            "ServerName",
            "UserData",
            "ComputeZone",
            mock_server_group,
            mock_network,
            MOCK_META,
        )

        self.assertEqual(server.id, 7)
        self.mock_cloud.os_cloud.compute.create_server.assert_called_with(
            availability_zone="ComputeZone",
            name="ServerName",
            image_id=self.mock_cloud.os_image.id,
            flavor_id=self.mock_cloud.os_flavor.id,
            networks=[{"uuid": 5678}],
            user_data="UserData",
            scheduler_hints={"group": 1234},
        )
        self.mock_cloud.os_cloud.compute.wait_for_server.assert_called_with(
            server,
            interval=MOCK_META.interval,
            wait=MOCK_META.timeout,
        )

    def test_create_server_1(self):
        self.mock_cloud.os_cloud.compute.create_server.return_value = MockServer(7)
        mock_server_group = MagicMock()
        mock_server_group.id = 1234
        mock_network = MagicMock()
        mock_network.id = 5678

        server = create_server(
            self.mock_cloud,
            "ServerName",
            "UserData",
            "ComputeZone",
            mock_server_group,
            mock_network,
            MOCK_META_2,
        )

        self.assertEqual(server.id, 7)
        self.mock_cloud.os_cloud.compute.create_server.assert_called_with(
            availability_zone="ComputeZone",
            name="ServerName",
            image_id=self.mock_cloud.os_image.id,
            flavor_id=self.mock_cloud.os_flavor.id,
            networks=[{"uuid": 5678}],
            user_data="UserData",
            scheduler_hints={"group": 1234},
        )
        self.mock_cloud.os_cloud.compute.wait_for_server.assert_called_with(
            server,
            interval=MOCK_META.interval,
            wait=MOCK_META.timeout,
        )


class TestDelete(TestBase):

    @patch("openstack_simple_stress.main.create_volume")
    @patch("openstack_simple_stress.main.create_server")
    def test_delete_server_0(self, mock_create_server, mock_create_volume):
        mock_create_server.return_value = MockServer(7)
        mock_create_volume.return_value = MockVolume(17)

        instance = create(
            self.mock_cloud,
            "ServerName",
            "UserData",
            "ComputeZone",
            True,
            5,
            "StorageZone",
            50,
            MagicMock(),
            "VolumeType",
            MagicMock(),
            MOCK_META,
        )

        delete_server(instance, MOCK_META)

        self.mock_cloud.os_cloud.compute.delete_server.assert_called_with(
            instance.server
        )
        self.mock_cloud.os_cloud.compute.wait_for_delete.assert_called_with(
            instance.server, interval=MOCK_META.interval, wait=MOCK_META.timeout
        )
        self.assertEqual(
            self.mock_cloud.os_cloud.block_storage.delete_volume.call_count, 5
        )
        self.assertEqual(
            self.mock_cloud.os_cloud.block_storage.wait_for_delete.call_count, 5
        )
        self.mock_cloud.os_cloud.block_storage.delete_volume.assert_called_with(
            instance.volumes[3]
        )
        self.mock_cloud.os_cloud.block_storage.wait_for_delete.assert_called_with(
            instance.volumes[3], interval=MOCK_META.interval, wait=MOCK_META.timeout
        )


if __name__ == "__main__":
    unittest.main()
