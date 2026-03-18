import base64
import itertools
import unittest
from unittest.mock import MagicMock, patch

import typer
from typer.testing import CliRunner

from openstack_simple_stress.main import run


app = typer.Typer()
app.command()(run)


def _make_time_mock(mock_time):
    """Configure a time mock so time.time() auto-increments and sleep is a no-op.

    Each call to time.time() returns 0, 100000, 200000, ...
    This ensures any duration-based while loop exits on the first iteration.
    """
    counter = itertools.count(0, 100000)
    mock_time.time.side_effect = lambda: next(counter)
    mock_time.sleep = MagicMock()


class TestBurnin(unittest.TestCase):

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

    def _make_instance(self):
        instance = MagicMock()
        instance.server.id = "srv-1"
        instance.server_name = "simple-stress-0"
        instance.volumes = []
        return instance

    def test_burnin_flag_in_help(self):
        result = self.runner.invoke(app, ["--help"])
        self.assertEqual(result.exit_code, 0, (result, result.stdout))
        self.assertIn("--burnin", result.stdout)
        self.assertIn("--burnin-duration", result.stdout)

    @patch("openstack_simple_stress.main.time")
    @patch("openstack_simple_stress.main.create")
    def test_burnin_creates_instances(self, mock_create, mock_time):
        """Burnin mode should create the requested number of instances."""
        mock_create.return_value = self._make_instance()
        _make_time_mock(mock_time)

        result = self.runner.invoke(
            app, ["--burnin", "--number=3", "--burnin-duration=1"]
        )
        self.assertEqual(result.exit_code, 0, (result, result.stdout))
        self.assertEqual(mock_create.call_count, 3)

    @patch("openstack_simple_stress.main.time")
    @patch("openstack_simple_stress.main.create")
    def test_burnin_user_data_contains_stress_ng(self, mock_create, mock_time):
        """Burnin user data should install and run stress-ng."""
        mock_create.return_value = self._make_instance()
        _make_time_mock(mock_time)

        result = self.runner.invoke(
            app, ["--burnin", "--number=1", "--burnin-duration=1"]
        )
        self.assertEqual(result.exit_code, 0, (result, result.stdout))

        b64_user_data = mock_create.call_args[0][2]
        user_data = base64.b64decode(b64_user_data).decode("utf-8")

        self.assertIn("#!/bin/bash", user_data)
        self.assertIn("apt-get install -y stress-ng", user_data)
        self.assertIn("NUMBER_OF_CPUS=$(nproc --all)", user_data)
        self.assertIn("/usr/bin/stress-ng --cpu $NUMBER_OF_CPUS --timeout", user_data)

    @patch("openstack_simple_stress.main.time")
    @patch("openstack_simple_stress.main.create")
    def test_burnin_duration_sets_stress_ng_timeout(self, mock_create, mock_time):
        """burnin-duration in hours should be converted to seconds for stress-ng."""
        mock_create.return_value = self._make_instance()
        _make_time_mock(mock_time)

        result = self.runner.invoke(
            app, ["--burnin", "--number=1", "--burnin-duration=24"]
        )
        self.assertEqual(result.exit_code, 0, (result, result.stdout))

        b64_user_data = mock_create.call_args[0][2]
        user_data = base64.b64decode(b64_user_data).decode("utf-8")
        # 24h * 3600 = 86400
        self.assertIn("--timeout 86400", user_data)

    @patch("openstack_simple_stress.main.time")
    @patch("openstack_simple_stress.main.create")
    def test_burnin_default_duration_48h(self, mock_create, mock_time):
        """Default burnin-duration is 48h, so stress-ng timeout should be 172800s."""
        mock_create.return_value = self._make_instance()
        _make_time_mock(mock_time)

        result = self.runner.invoke(app, ["--burnin", "--number=1"])
        self.assertEqual(result.exit_code, 0, (result, result.stdout))

        b64_user_data = mock_create.call_args[0][2]
        user_data = base64.b64decode(b64_user_data).decode("utf-8")
        # 48h * 3600 = 172800
        self.assertIn("--timeout 172800", user_data)

    @patch("openstack_simple_stress.main.time")
    @patch("openstack_simple_stress.main.create")
    def test_burnin_does_not_delete_during_creation(self, mock_create, mock_time):
        """In burnin mode, meta.delete should be False during instance creation."""
        mock_create.return_value = self._make_instance()
        _make_time_mock(mock_time)

        result = self.runner.invoke(
            app, ["--burnin", "--number=1", "--burnin-duration=1"]
        )
        self.assertEqual(result.exit_code, 0, (result, result.stdout))

        # meta is the 12th positional arg (index 11) to create()
        call_args = mock_create.call_args[0]
        meta = call_args[11]
        self.assertFalse(meta.delete)

    @patch("openstack_simple_stress.main.delete_server")
    @patch("openstack_simple_stress.main.time")
    @patch("openstack_simple_stress.main.create")
    def test_burnin_deletes_after_wait(self, mock_create, mock_time, mock_delete):
        """Burnin should delete instances after the wait duration."""
        mock_create.return_value = self._make_instance()
        _make_time_mock(mock_time)

        result = self.runner.invoke(
            app, ["--burnin", "--number=2", "--burnin-duration=1"]
        )
        self.assertEqual(result.exit_code, 0, (result, result.stdout))
        self.assertEqual(mock_delete.call_count, 2)

    @patch("openstack_simple_stress.main.delete_server")
    @patch("openstack_simple_stress.main.time")
    @patch("openstack_simple_stress.main.create")
    def test_burnin_no_cleanup_skips_instance_deletion(
        self, mock_create, mock_time, mock_delete
    ):
        """With --no-cleanup, burnin should not delete instances."""
        mock_create.return_value = self._make_instance()
        _make_time_mock(mock_time)

        result = self.runner.invoke(
            app, ["--burnin", "--number=2", "--burnin-duration=1", "--no-cleanup"]
        )
        self.assertEqual(result.exit_code, 0, (result, result.stdout))
        mock_delete.assert_not_called()

    @patch("openstack_simple_stress.main.delete_server")
    @patch("openstack_simple_stress.main.time")
    @patch("openstack_simple_stress.main.create")
    def test_burnin_no_cleanup_skips_infra_deletion(
        self, mock_create, mock_time, mock_delete
    ):
        """With --no-cleanup in burnin, infrastructure should not be deleted."""
        mock_create.return_value = self._make_instance()
        _make_time_mock(mock_time)

        result = self.runner.invoke(
            app, ["--burnin", "--number=1", "--burnin-duration=1", "--no-cleanup"]
        )
        self.assertEqual(result.exit_code, 0, (result, result.stdout))
        self.mock_os_cloud.compute.delete_server_group.assert_not_called()
        self.mock_os_cloud.network.delete_subnet.assert_not_called()
        self.mock_os_cloud.network.delete_network.assert_not_called()

    @patch("openstack_simple_stress.main.delete_server")
    @patch("openstack_simple_stress.main.time")
    @patch("openstack_simple_stress.main.create")
    def test_burnin_with_cleanup_deletes_infra(
        self, mock_create, mock_time, mock_delete
    ):
        """With cleanup (default) in burnin, infrastructure should be deleted."""
        mock_create.return_value = self._make_instance()
        _make_time_mock(mock_time)

        mock_server_group = MagicMock()
        self.mock_os_cloud.compute.create_server_group.return_value = mock_server_group

        result = self.runner.invoke(
            app, ["--burnin", "--number=1", "--burnin-duration=1"]
        )
        self.assertEqual(result.exit_code, 0, (result, result.stdout))
        self.mock_os_cloud.compute.delete_server_group.assert_called_once_with(
            mock_server_group
        )

    @patch("openstack_simple_stress.main.time")
    @patch("openstack_simple_stress.main.create")
    def test_burnin_report_params(self, mock_create, mock_time):
        """Report params should show burnin mode and duration."""
        mock_create.return_value = self._make_instance()
        _make_time_mock(mock_time)

        with patch("openstack_simple_stress.main.Report") as mock_report_cls:
            mock_report = MagicMock()
            mock_report.track = MagicMock(
                return_value=MagicMock(
                    __enter__=MagicMock(),
                    __exit__=MagicMock(return_value=False),
                )
            )
            mock_report.end_time = 99999
            mock_report.start_time = 0
            mock_report_cls.return_value = mock_report

            result = self.runner.invoke(
                app,
                [
                    "--burnin",
                    "--number=1",
                    "--burnin-duration=24",
                ],
            )
            self.assertEqual(result.exit_code, 0, (result, result.stdout))

            params = mock_report.params
            self.assertEqual(params["mode"], "burnin")
            self.assertEqual(params["burnin_duration"], "24h")

    @patch("openstack_simple_stress.main.time")
    @patch("openstack_simple_stress.main.create")
    def test_burnin_profile(self, mock_create, mock_time):
        """The builtin burnin profile should activate burnin mode."""
        mock_create.return_value = self._make_instance()
        _make_time_mock(mock_time)

        result = self.runner.invoke(app, ["--profile=burnin"])
        self.assertEqual(result.exit_code, 0, (result, result.stdout))
        # Profile has number=10
        self.assertEqual(mock_create.call_count, 10)

        # Verify burnin user data (stress-ng script)
        b64_user_data = mock_create.call_args[0][2]
        user_data = base64.b64decode(b64_user_data).decode("utf-8")
        self.assertIn("stress-ng", user_data)

    @patch("openstack_simple_stress.main.time")
    @patch("openstack_simple_stress.main.create")
    def test_burnin_cli_overrides_profile(self, mock_create, mock_time):
        """CLI args should override burnin profile values."""
        mock_create.return_value = self._make_instance()
        _make_time_mock(mock_time)

        result = self.runner.invoke(app, ["--profile=burnin", "--number=2"])
        self.assertEqual(result.exit_code, 0, (result, result.stdout))
        # CLI --number=2 should override profile's number=10
        self.assertEqual(mock_create.call_count, 2)

    @patch("openstack_simple_stress.main.time")
    @patch("openstack_simple_stress.main.create")
    def test_burnin_user_data_has_debian_frontend(self, mock_create, mock_time):
        """Burnin user data should set DEBIAN_FRONTEND=noninteractive."""
        mock_create.return_value = self._make_instance()
        _make_time_mock(mock_time)

        result = self.runner.invoke(
            app, ["--burnin", "--number=1", "--burnin-duration=1"]
        )
        self.assertEqual(result.exit_code, 0, (result, result.stdout))

        b64_user_data = mock_create.call_args[0][2]
        user_data = base64.b64decode(b64_user_data).decode("utf-8")
        self.assertIn("DEBIAN_FRONTEND=noninteractive", user_data)
        self.assertIn("apt-get update", user_data)

    def test_burnin_duration_zero_rejected(self):
        """--burnin-duration=0 should be rejected."""
        result = self.runner.invoke(
            app, ["--burnin", "--number=1", "--burnin-duration=0"]
        )
        self.assertNotEqual(result.exit_code, 0)

    def test_burnin_with_mode_rejected(self):
        """--burnin and --mode cannot be used together."""
        result = self.runner.invoke(app, ["--burnin", "--number=1", "--mode=block"])
        self.assertNotEqual(result.exit_code, 0)


if __name__ == "__main__":
    unittest.main()
