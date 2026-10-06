import unittest
from unittest.mock import MagicMock, patch

import openstack.exceptions
import typer
from typer.testing import CliRunner

from openstack_simple_stress.main import Registry, run

app = typer.Typer()
app.command()(run)


def _server(name, run_id):
    s = MagicMock()
    s.name = name
    s.id = f"id-{name}"
    s.metadata = {"simple-stress-run": run_id}
    return s


class TestRegistry(unittest.TestCase):

    def test_add_remove_items(self):
        reg = Registry()
        reg.add("server", "a", "p-0", object())
        reg.add("volume", "b", "p-0-volume-0", object())
        self.assertEqual([r.id for r in reg.items()], ["a", "b"])
        self.assertEqual([r.id for r in reg.items("volume")], ["b"])
        reg.remove("a")
        self.assertEqual([r.id for r in reg.items()], ["b"])
        reg.remove("missing")  # no error


class TestOwnership(unittest.TestCase):

    def setUp(self):
        patcher = patch("openstack.connect")
        self.mock_connect = patcher.start()
        self.addCleanup(patcher.stop)
        self.os = MagicMock()
        self.mock_connect.return_value = self.os
        self.os.compute.get_server_console_output.return_value = (
            "The system is finally up"
        )
        self.os.network.find_network.return_value = None
        self.os.network.find_subnet.return_value = None
        self.os.compute.find_server_group.return_value = None
        uuid_patch = patch("openstack_simple_stress.main.uuid")
        mock_uuid = uuid_patch.start()
        self.addCleanup(uuid_patch.stop)
        mock_uuid.uuid4.return_value = "run-1"
        self.runner = CliRunner()

    def test_server_failing_after_create_is_deleted(self):
        self.os.compute.wait_for_server.side_effect = Exception("ERROR state")

        result = self.runner.invoke(app, [])
        self.assertEqual(result.exit_code, 1, (result, result.stdout))
        created = self.os.compute.create_server.return_value
        self.os.compute.delete_server.assert_called_once_with(created)

    def test_volume_attach_failure_deletes_server_and_volume(self):
        self.os.attach_volume.side_effect = Exception("attach failed")

        result = self.runner.invoke(app, [])
        self.assertEqual(result.exit_code, 1, (result, result.stdout))
        self.os.compute.delete_server.assert_called_once_with(
            self.os.compute.create_server.return_value
        )
        self.os.block_storage.delete_volume.assert_called_once_with(
            self.os.block_storage.create_volume.return_value
        )

    def test_setup_failure_deletes_created_network(self):
        self.os.compute.create_server_group.side_effect = (
            openstack.exceptions.HttpException(message="boom", http_status=500)
        )

        result = self.runner.invoke(app, [])
        self.assertEqual(result.exit_code, 1, (result, result.stdout))
        self.os.compute.create_server.assert_not_called()
        self.os.network.delete_network.assert_called_once_with(
            self.os.network.create_network.return_value, ignore_missing=False
        )

    def test_sweep_deletes_marked_server_missed_by_registry(self):
        stray = _server("simple-stress-7", "run-1")
        self.os.compute.servers.return_value = [stray]

        result = self.runner.invoke(app, [])
        self.assertEqual(result.exit_code, 0, (result, result.stdout))
        self.os.compute.delete_server.assert_any_call(stray)

    def test_sweep_ignores_other_runs(self):
        other = _server("simple-stress-7", "run-OTHER")
        self.os.compute.servers.return_value = [other]

        result = self.runner.invoke(app, [])
        self.assertEqual(result.exit_code, 0, (result, result.stdout))
        for call in self.os.compute.delete_server.call_args_list:
            self.assertIsNot(call.args[0], other)

    def test_undeletable_leftover_exits_1_and_is_listed(self):
        self.os.network.delete_network.side_effect = Exception("409")

        result = self.runner.invoke(app, [])
        self.assertEqual(result.exit_code, 1, (result, result.stdout))
        self.assertIn("--clean", result.stdout)

    def test_retention_lists_kept_resources_and_exits_0(self):
        result = self.runner.invoke(app, ["--no-delete", "--no-cleanup"])
        self.assertEqual(result.exit_code, 0, (result, result.stdout))
        self.assertIn("Kept", result.stdout)

    def test_reused_network_is_never_deleted(self):
        existing = MagicMock()
        self.os.network.find_network.return_value = existing
        self.os.compute.wait_for_server.side_effect = Exception("ERROR state")

        result = self.runner.invoke(app, [])
        self.assertEqual(result.exit_code, 1, (result, result.stdout))
        for call in self.os.network.delete_network.call_args_list:
            self.assertIsNot(call.args[0], existing)

    def test_already_deleted_server_is_not_a_leftover(self):
        self.os.compute.wait_for_delete.side_effect = (
            openstack.exceptions.NotFoundException("gone")
        )

        result = self.runner.invoke(app, ["--no-volume"])
        self.assertEqual(result.exit_code, 0, (result, result.stdout))

    def test_failed_network_lookup_exits_1(self):
        self.os.network.find_network.side_effect = openstack.exceptions.HttpException(
            message="boom", http_status=500
        )

        result = self.runner.invoke(app, [])
        self.assertEqual(result.exit_code, 1, (result, result.stdout))
        self.os.compute.create_server.assert_not_called()

    # Only resources whose earlier delete failed are still in the registry when
    # the sweep runs (completed instances are deleted before it), so these
    # tests make the first delete fail and the sweep's listing fail too.

    def test_server_listing_failure_still_deletes_registered_server(self):
        self.os.attach_volume.side_effect = Exception("attach failed")
        self.os.compute.delete_server.side_effect = [Exception("busy"), None]
        self.os.compute.servers.side_effect = Exception("list failed")

        result = self.runner.invoke(app, [])
        self.assertEqual(result.exit_code, 1, (result, result.stdout))
        created = self.os.compute.create_server.return_value
        self.assertEqual(self.os.compute.delete_server.call_count, 2)
        self.os.compute.delete_server.assert_called_with(created)
        self.os.network.delete_network.assert_called_once()

    def test_volume_listing_failure_still_deletes_registered_volume(self):
        self.os.attach_volume.side_effect = Exception("attach failed")
        self.os.block_storage.delete_volume.side_effect = [Exception("busy"), None]
        self.os.block_storage.volumes.side_effect = Exception("list failed")

        result = self.runner.invoke(app, [])
        self.assertEqual(result.exit_code, 1, (result, result.stdout))
        created = self.os.block_storage.create_volume.return_value
        self.assertEqual(self.os.block_storage.delete_volume.call_count, 2)
        self.os.block_storage.delete_volume.assert_called_with(created)
        self.os.network.delete_network.assert_called_once()

    def test_untracked_failure_exits_1(self):
        # The server refresh after attaching runs outside report.track().
        self.os.compute.get_server.side_effect = Exception("refresh failed")

        result = self.runner.invoke(app, [])
        self.assertEqual(result.exit_code, 1, (result, result.stdout))
        self.os.compute.delete_server.assert_called_once_with(
            self.os.compute.create_server.return_value
        )
        self.assertIn("Failed servers: 1", result.stdout)

    def test_not_found_during_attach_is_a_failure(self):
        self.os.attach_volume.side_effect = openstack.exceptions.NotFoundException(
            "volume vanished"
        )

        result = self.runner.invoke(app, [])
        self.assertEqual(result.exit_code, 1, (result, result.stdout))

    @patch("openstack_simple_stress.main.shutdown_requested", True)
    def test_abort_after_setup_cleans_infrastructure(self):
        result = self.runner.invoke(app, ["--number=2"])
        self.assertEqual(result.exit_code, 130, (result, result.stdout))
        self.os.network.delete_subnet.assert_called_once()
        self.os.network.delete_network.assert_called_once()
