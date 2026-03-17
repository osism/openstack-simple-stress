# SPDX-License-Identifier: AGPL-3.0-or-later

import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from importlib import resources
import ipaddress
from pathlib import Path

import signal
import statistics
import sys
import threading
import time
from typing import List

import click
from keystoneauth1.exceptions.catalog import EndpointNotFound
from loguru import logger
import openstack
from rich.console import Console
from rich.table import Table
import typer
from typing_extensions import Annotated
import yaml

log_fmt = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | "
    "<level>{message}</level>"
)

logger.remove()
logger.add(sys.stderr, format=log_fmt, level="INFO", colorize=True)

shutdown_requested = False

VALID_PROFILE_KEYS = {
    "clean",
    "no_cleanup",
    "debug",
    "no_delete",
    "volume",
    "no_volume",
    "no_boot_volume",
    "no_wait",
    "interval",
    "number",
    "parallel",
    "mode",
    "timeout",
    "volume_number",
    "volume_size",
    "cloud",
    "flavor",
    "image",
    "subnet_cidr",
    "prefix",
    "compute_zone",
    "storage_zone",
    "affinity",
    "volume_type",
    "boot_volume_size",
}

PROFILE_KEY_TO_PARAM = {
    "cloud": "cloud_name",
    "flavor": "flavor_name",
    "image": "image_name",
}


def _resolve_builtin_profile(name: str) -> Path | None:
    """Resolve a built-in profile name to its path using importlib.resources."""
    candidates = [name]
    if not name.endswith((".yaml", ".yml")):
        candidates.append(f"{name}.yaml")

    for candidate in candidates:
        ref = resources.files("openstack_simple_stress.profiles").joinpath(candidate)
        try:
            with resources.as_file(ref) as p:
                if p.exists():
                    return p
        except (FileNotFoundError, TypeError):
            continue
    return None


def load_profile(profile_path: str) -> dict:
    """Load a YAML profile and return the parameter dict."""
    path = Path(profile_path)

    if not path.exists():
        resolved = _resolve_builtin_profile(profile_path)
        if resolved is not None:
            path = resolved

    if not path.exists():
        logger.error(f"Profile '{profile_path}' not found")
        sys.exit(1)

    with open(path) as f:
        data = yaml.safe_load(f)

    if data is None:
        return {}

    if not isinstance(data, dict):
        logger.error(f"Profile '{path}' must be a YAML mapping")
        sys.exit(1)

    unknown = set(data.keys()) - VALID_PROFILE_KEYS
    if unknown:
        logger.warning(f"Unknown keys in profile: {', '.join(sorted(unknown))}")

    logger.info(f"Loaded profile '{path}'")
    return data


def signal_handler(signum, frame):
    global shutdown_requested

    logger.warning("\nCTRL+C received - Do you want to abort the test?")
    try:
        response = input("Abort? (y/N): ").strip().lower()
        if response in ["y", "yes"]:
            shutdown_requested = True
            logger.info(
                "Graceful shutdown initiated - current iteration will be aborted and cleanup performed..."
            )
        else:
            logger.info("Continuing with test...")
    except (EOFError, KeyboardInterrupt):
        shutdown_requested = True
        logger.info("Graceful shutdown initiated...")


# source: https://stackoverflow.com/questions/18466079/can-i-change-the-connection-pool-size-for-pythons-requests-module  # noqa
def patch_http_connection_pool(**constructor_kwargs) -> None:
    """
    This allows to override the default parameters of the
    HTTPConnectionPool constructor.
    For example, to increase the poolsize to fix problems
    with "HttpConnectionPool is full, discarding connection"
    call this function with maxsize=16 (or whatever size
    you want to give to the connection pool)
    """
    from urllib3 import connectionpool, poolmanager

    class MyHTTPConnectionPool(connectionpool.HTTPConnectionPool):
        def __init__(self, *args, **kwargs):
            kwargs.update(constructor_kwargs)
            super(MyHTTPConnectionPool, self).__init__(*args, **kwargs)

    poolmanager.pool_classes_by_scheme["http"] = MyHTTPConnectionPool


# source: https://stackoverflow.com/questions/18466079/can-i-change-the-connection-pool-size-for-pythons-requests-module  # noqa
def patch_https_connection_pool(**constructor_kwargs) -> None:
    """
    This allows to override the default parameters of the
    HTTPConnectionPool constructor.
    For example, to increase the poolsize to fix problems
    with "HttpSConnectionPool is full, discarding connection"
    call this function with maxsize=16 (or whatever size
    you want to give to the connection pool)
    """
    from urllib3 import connectionpool, poolmanager

    class MyHTTPSConnectionPool(connectionpool.HTTPSConnectionPool):
        def __init__(self, *args, **kwargs):
            kwargs.update(constructor_kwargs)
            super(MyHTTPSConnectionPool, self).__init__(*args, **kwargs)

    poolmanager.pool_classes_by_scheme["https"] = MyHTTPSConnectionPool


class Meta:

    def __init__(self, wait: bool, interval: int, timeout: int, delete: bool):
        self.wait = wait
        self.interval = interval
        self.timeout = timeout
        self.delete = delete


@dataclass
class OperationRecord:
    operation: str
    resource_name: str
    duration: float
    success: bool
    error: str | None = None


@contextmanager
def _noop_track(operation: str, resource_name: str):
    yield


class Report:

    def __init__(self):
        self._lock = threading.Lock()
        self._records: list[OperationRecord] = []
        self.start_time: float = time.time()
        self.end_time: float | None = None
        self.params: dict = {}

    def record(
        self,
        operation: str,
        resource_name: str,
        duration: float,
        success: bool,
        error: str | None = None,
    ) -> None:
        with self._lock:
            self._records.append(
                OperationRecord(operation, resource_name, duration, success, error)
            )

    @contextmanager
    def track(self, operation: str, resource_name: str):
        start = time.time()
        try:
            yield
            self.record(operation, resource_name, time.time() - start, True)
        except Exception as e:
            self.record(operation, resource_name, time.time() - start, False, str(e))
            raise

    def finalize(self) -> None:
        self.end_time = time.time()

    def print_report(self) -> None:
        if not self._records:
            return

        console = Console()
        total_runtime = (self.end_time or time.time()) - self.start_time

        # Determine status
        errors = [r for r in self._records if not r.success]
        if errors:
            status = "COMPLETED WITH ERRORS"
        else:
            status = "COMPLETED"

        # Header
        console.print()
        console.print("=" * 80)
        console.print("OPENSTACK STRESS TEST REPORT", justify="center", style="bold")
        console.print("=" * 80)

        # Test Parameters
        p = self.params
        console.print()
        console.print("[bold]Test Parameters[/bold]")
        if p.get("profile"):
            console.print(f"  Profile: {p.get('profile')}")
        console.print(
            f"  Instances: {p.get('number', '?')} (parallel: {p.get('parallel', '?')},"
            f" mode: {p.get('mode', '?')})"
        )
        console.print(
            f"  Flavor: {p.get('flavor', '?')} | Image: {p.get('image', '?')}"
        )
        console.print(
            f"  Volumes per instance: {p.get('volume_number', '?')}"
            f" (size: {p.get('volume_size', '?')} GB,"
            f" type: {p.get('volume_type', '?')})"
        )
        console.print(
            f"  Boot from volume: {'yes' if p.get('boot_from_volume') else 'no'}"
            f" (size: {p.get('boot_volume_size', '?')} GB)"
        )
        console.print(f"  Cloud: {p.get('cloud', '?')}")
        console.print(f"  Affinity: {p.get('affinity', '?')}")
        console.print(
            f"  Delete: {'yes' if p.get('delete') else 'no'}"
            f" | Cleanup: {'yes' if p.get('cleanup') else 'no'}"
        )
        console.print(f"  Status: {status}")
        console.print()
        console.print(f"Total Runtime: {total_runtime:.2f}s")
        console.print("=" * 80)

        # Collect operations in logical order
        op_order = [
            "network_create",
            "subnet_create",
            "server_group_create",
            "server_create",
            "server_wait_active",
            "server_wait_boot",
            "volume_create",
            "volume_attach",
            "server_delete",
            "volume_delete",
            "server_group_delete",
            "subnet_delete",
            "network_delete",
        ]

        # Group records by operation
        by_op: dict[str, list[OperationRecord]] = {}
        for r in self._records:
            by_op.setdefault(r.operation, []).append(r)

        # Build table
        table = Table(title="Operation Statistics")
        table.add_column("Operation", style="cyan")
        table.add_column("Count", justify="right")
        table.add_column("Errors", justify="right", style="red")
        table.add_column("Avg (s)", justify="right")
        table.add_column("Min (s)", justify="right")
        table.add_column("Max (s)", justify="right")
        table.add_column("Med (s)", justify="right")
        table.add_column("P95 (s)", justify="right")

        total_count = 0
        total_errors = 0

        # Add rows in logical order, then any extras
        ordered_ops = [op for op in op_order if op in by_op]
        extra_ops = [op for op in by_op if op not in op_order]
        for op in ordered_ops + extra_ops:
            records = by_op[op]
            durations = [r.duration for r in records]
            err_count = sum(1 for r in records if not r.success)
            total_count += len(records)
            total_errors += err_count

            avg = statistics.mean(durations)
            med = statistics.median(durations)
            mn = min(durations)
            mx = max(durations)
            if len(durations) >= 2:
                p95 = statistics.quantiles(durations, n=20)[-1]
            else:
                p95 = durations[0]

            err_style = "red" if err_count > 0 else ""
            table.add_row(
                op,
                str(len(records)),
                (
                    f"[{err_style}]{err_count}[/{err_style}]"
                    if err_style
                    else str(err_count)
                ),
                f"{avg:.2f}",
                f"{mn:.2f}",
                f"{mx:.2f}",
                f"{med:.2f}",
                f"{p95:.2f}",
            )

        table.add_section()
        table.add_row(
            "TOTAL",
            str(total_count),
            str(total_errors),
            "",
            "",
            "",
            "",
            "",
        )

        console.print()
        console.print(table)

        # Error details
        if errors:
            console.print()
            console.print(f"[bold red]Errors ({len(errors)})[/bold red]")
            for r in errors:
                console.print(f"  [{r.operation}] {r.resource_name}: {r.error}")

        console.print("=" * 80)
        console.print()


class Cloud:

    def __init__(self, cloud_name: str, flavor_name: str, image_name: str):
        self.os_cloud = openstack.connect(cloud=cloud_name)

        logger.info(f"Checking flavor {flavor_name}")
        self.os_flavor = self.os_cloud.get_flavor(flavor_name)
        if self.os_flavor is None:
            logger.error(f"Flavor '{flavor_name}' not found")
            sys.exit(1)
        logger.info(f"flavor.id = {self.os_flavor.id}")

        logger.info(f"Checking image {image_name}")
        self.os_image = self.os_cloud.get_image(image_name)
        if self.os_image is None:
            logger.error(f"Image '{image_name}' not found")
            sys.exit(1)
        logger.info(f"image.id = {self.os_image.id}")


class Instance:

    def __init__(
        self,
        cloud: Cloud,
        name: str,
        user_data: str,
        compute_zone: str,
        server_group: openstack.compute.v2.server_group.ServerGroup,
        network: openstack.network.v2.network.Network,
        meta: Meta,
        boot_volume_size: int = 20,
        storage_zone: str = "nova",
        volume_type: str = "__DEFAULT__",
        boot_from_volume: bool = True,
        report: Report | None = None,
    ):
        self.cloud = cloud

        self.server = create_server(
            self.cloud,
            name,
            user_data,
            compute_zone,
            server_group,
            network,
            meta,
            boot_volume_size,
            storage_zone,
            volume_type,
            boot_from_volume,
            report=report,
        )
        self.server_name = name

        self.volumes: List[openstack.block_storage.v2.volume.Volume] = []

    def add_volume(
        self,
        name: str,
        storage_zone: str,
        volume_size: int,
        volume_type: str,
        meta: Meta,
        report: Report | None = None,
    ) -> None:
        volume = create_volume(
            self.cloud,
            name,
            storage_zone,
            volume_size,
            volume_type,
            meta,
            report=report,
        )
        self.volumes.append(volume)

    def attach_volumes(self, report: Report | None = None) -> None:
        track = report.track if report else _noop_track
        for volume in self.volumes:
            logger.info(
                f"Attaching volume {volume.id} to server {self.server.id} ({self.server_name})"
            )
            with track("volume_attach", f"{self.server_name}-vol-{volume.id}"):
                self.cloud.os_cloud.attach_volume(self.server, volume)

            logger.info(f"Refreshing details of {self.server.id} ({self.server_name})")
            self.server = self.cloud.os_cloud.compute.get_server(self.server.id)


def create(
    cloud: Cloud,
    name: str,
    user_data: str,
    compute_zone: str,
    volume: bool,
    volume_number: int,
    storage_zone: str,
    volume_size: int,
    server_group: openstack.compute.v2.server_group.ServerGroup,
    volume_type: str,
    network: openstack.network.v2.network.Network,
    meta: Meta,
    boot_volume_size: int = 20,
    boot_from_volume: bool = True,
    report: Report | None = None,
) -> Instance:

    instance = Instance(
        cloud,
        name,
        user_data,
        compute_zone,
        server_group,
        network,
        meta,
        boot_volume_size,
        storage_zone,
        volume_type,
        boot_from_volume,
        report=report,
    )

    if volume:
        for x in range(volume_number):
            instance.add_volume(
                f"{name}-volume-{x}",
                storage_zone,
                volume_size,
                volume_type,
                meta,
                report=report,
            )

    instance.attach_volumes(report=report)

    if meta.delete:
        delete_server(instance, meta, report=report)
    else:
        logger.info(
            f"Skipping deletion of server {instance.server.id} ({instance.server_name})"
        )
        for v in instance.volumes:
            logger.info(
                f"Skipping deletion of volume {v.id} from server {instance.server.id} ({instance.server_name})"
            )

    return instance


def create_volume(
    cloud: Cloud,
    name: str,
    storage_zone: str,
    volume_size: int,
    volume_type: str,
    meta: Meta,
    report: Report | None = None,
) -> openstack.block_storage.v2.volume.Volume:
    logger.info(f"Creating volume {name}")
    track = report.track if report else _noop_track

    with track("volume_create", name):
        volume = cloud.os_cloud.block_storage.create_volume(
            availability_zone=storage_zone,
            name=name,
            size=volume_size,
            volume_type=volume_type,
        )

        logger.info(f"Waiting for volume {volume.id}")
        cloud.os_cloud.block_storage.wait_for_status(
            volume, status="available", interval=meta.interval, wait=meta.timeout
        )

    return volume


def create_server(
    cloud: Cloud,
    name: str,
    user_data: str,
    compute_zone: str,
    server_group: openstack.compute.v2.server_group.ServerGroup,
    network: openstack.network.v2.network.Network,
    meta: Meta,
    boot_volume_size: int = 20,
    storage_zone: str = "nova",
    volume_type: str = "__DEFAULT__",
    boot_from_volume: bool = True,
    report: Report | None = None,
) -> openstack.compute.v2.server.Server:
    track = report.track if report else _noop_track

    if boot_from_volume:
        logger.info(
            f"Creating server {name} with boot from volume (size: {boot_volume_size}GB)"
        )

        # Create block device mapping for boot from volume
        block_device_mapping = [
            {
                "uuid": cloud.os_image.id,
                "source_type": "image",
                "destination_type": "volume",
                "boot_index": 0,
                "volume_size": boot_volume_size,
                "delete_on_termination": True,
            }
        ]

        # Add volume_type if not default
        if volume_type != "__DEFAULT__":
            block_device_mapping[0]["volume_type"] = volume_type

        with track("server_create", name):
            server = cloud.os_cloud.compute.create_server(
                availability_zone=compute_zone,
                name=name,
                flavor_id=cloud.os_flavor.id,
                networks=[{"uuid": network.id}],
                user_data=user_data,
                scheduler_hints={"group": server_group.id},
                block_device_mapping=block_device_mapping,
            )
    else:
        logger.info(f"Creating server {name} with boot from local storage")

        with track("server_create", name):
            server = cloud.os_cloud.compute.create_server(
                availability_zone=compute_zone,
                name=name,
                flavor_id=cloud.os_flavor.id,
                image_id=cloud.os_image.id,
                networks=[{"uuid": network.id}],
                user_data=user_data,
                scheduler_hints={"group": server_group.id},
            )

    logger.info(f"Waiting for server {server.id} ({name})")
    with track("server_wait_active", name):
        cloud.os_cloud.compute.wait_for_server(
            server, interval=meta.interval, wait=meta.timeout
        )

    if meta.wait:
        logger.info(f"Waiting for boot of {server.id} ({name})")
        with track("server_wait_boot", name):
            while True:
                console = cloud.os_cloud.compute.get_server_console_output(server)
                if "Failed to run module scripts-user" in str(console):
                    logger.error(f"Failed tests for {server.id} ({name})")
                if "The system is finally up" in str(console):
                    break
                time.sleep(1.0)

    return server


def delete_server(instance: Instance, meta: Meta, report: Report | None = None) -> None:
    logger.info(f"Deleting server {instance.server.id} ({instance.server.name})")
    track = report.track if report else _noop_track

    with track("server_delete", instance.server_name):
        instance.cloud.os_cloud.compute.delete_server(instance.server)
        logger.info(
            f"Waiting for deletion of server {instance.server.id} ({instance.server_name})"
        )
        instance.cloud.os_cloud.compute.wait_for_delete(
            instance.server, interval=meta.interval, wait=meta.timeout
        )

    for volume in instance.volumes:
        logger.info(
            f"Deleting volume {volume.id} from server {instance.server.id} ({instance.server_name})"
        )
        with track("volume_delete", f"{instance.server_name}-vol-{volume.id}"):
            instance.cloud.os_cloud.block_storage.delete_volume(volume)
            logger.info(f"Waiting for deletion of volume {volume.id}")
            instance.cloud.os_cloud.block_storage.wait_for_delete(
                volume, interval=meta.interval, wait=meta.timeout
            )


class AffinitySetting(str, Enum):
    soft = "soft-affinity"
    soft_anti = "soft-anti-affinity"
    hard = "affinity"
    hard_anti = "anti-affinity"


class ExecutionMode(str, Enum):
    rolling = "rolling"
    block = "block"


def clean_resources(cloud_name: str, prefix: str, debug: bool) -> None:
    """Find and delete all resources from a previous run with the given prefix."""

    openstack.enable_logging(debug=debug, http_debug=debug)
    os_cloud = openstack.connect(cloud=cloud_name)

    console = Console()

    # Find all resources with the prefix
    resources: list[tuple[str, str, str, str]] = []

    logger.info(f"Searching for servers with prefix '{prefix}'...")
    servers = list(os_cloud.compute.servers(name=f"^{prefix}-"))
    for s in servers:
        resources.append(("Server", s.name, s.id, s.status))

    logger.info(f"Searching for volumes with prefix '{prefix}'...")
    matching_volumes = []
    try:
        volumes = list(os_cloud.block_storage.volumes(details=True))
        matching_volumes = [
            v for v in volumes if v.name and v.name.startswith(f"{prefix}-")
        ]
        for v in matching_volumes:
            resources.append(("Volume", v.name, v.id, v.status))
    except EndpointNotFound:
        logger.warning("Block storage service not available, skipping volume cleanup")

    logger.info(f"Searching for server group '{prefix}'...")
    server_group = os_cloud.compute.find_server_group(prefix)
    if server_group:
        resources.append(("Server Group", server_group.name, server_group.id, ""))

    subnet_name = f"{prefix}-subnet"
    logger.info(f"Searching for subnet '{subnet_name}'...")
    subnet = os_cloud.network.find_subnet(subnet_name)
    if subnet:
        resources.append(("Subnet", subnet.name, subnet.id, ""))

    logger.info(f"Searching for network '{prefix}'...")
    network = os_cloud.network.find_network(prefix)
    if network:
        resources.append(("Network", network.name, network.id, ""))

    if not resources:
        logger.info(f"No resources found with prefix '{prefix}'")
        return

    # Display found resources
    table = Table(title=f"Resources found with prefix '{prefix}'")
    table.add_column("Type", style="cyan")
    table.add_column("Name", style="green")
    table.add_column("ID")
    table.add_column("Status")

    for r_type, r_name, r_id, r_status in resources:
        table.add_row(r_type, r_name, r_id, r_status)

    console.print()
    console.print(table)
    console.print()

    # Ask for confirmation
    try:
        response = (
            input(f"Delete all {len(resources)} resource(s)? (y/N): ").strip().lower()
        )
    except (EOFError, KeyboardInterrupt):
        logger.info("\nAborted.")
        return

    if response not in ["y", "yes"]:
        logger.info("Aborted.")
        return

    # Delete in order: servers, volumes, server group, subnet, network
    for s in servers:
        try:
            logger.info(f"Deleting server {s.name} ({s.id})")
            os_cloud.compute.delete_server(s)
            os_cloud.compute.wait_for_delete(s)
            logger.info(f"Server {s.name} deleted")
        except Exception as e:
            logger.error(f"Error deleting server {s.name}: {e}")

    for v in matching_volumes:
        try:
            logger.info(f"Deleting volume {v.name} ({v.id})")
            os_cloud.block_storage.delete_volume(v)
            os_cloud.block_storage.wait_for_delete(v)
            logger.info(f"Volume {v.name} deleted")
        except Exception as e:
            logger.error(f"Error deleting volume {v.name}: {e}")

    if server_group:
        try:
            logger.info(
                f"Deleting server group {server_group.name} ({server_group.id})"
            )
            os_cloud.compute.delete_server_group(server_group)
            logger.info(f"Server group {server_group.name} deleted")
        except Exception as e:
            logger.error(f"Error deleting server group: {e}")

    if subnet:
        try:
            logger.info(f"Deleting subnet {subnet.name} ({subnet.id})")
            os_cloud.network.delete_subnet(subnet, ignore_missing=False)
            logger.info(f"Subnet {subnet.name} deleted")
        except Exception as e:
            logger.error(f"Error deleting subnet: {e}")

    if network:
        try:
            logger.info(f"Deleting network {network.name} ({network.id})")
            os_cloud.network.delete_network(network, ignore_missing=False)
            logger.info(f"Network {network.name} deleted")
        except Exception as e:
            logger.error(f"Error deleting network: {e}")

    logger.info("Cleanup completed")


def run(
    ctx: typer.Context,
    profile: Annotated[
        str, typer.Option("--profile", help="Path to a YAML profile file")
    ] = "",
    clean: Annotated[bool, typer.Option("--clean")] = False,
    no_cleanup: Annotated[bool, typer.Option("--no-cleanup")] = False,
    debug: Annotated[bool, typer.Option("--debug")] = False,
    no_delete: Annotated[bool, typer.Option("--no-delete")] = False,
    volume: Annotated[bool, typer.Option("--volume")] = True,
    no_volume: Annotated[bool, typer.Option("--no-volume")] = False,
    no_boot_volume: Annotated[bool, typer.Option("--no-boot-volume")] = False,
    no_wait: Annotated[bool, typer.Option("--no-wait")] = False,
    interval: Annotated[int, typer.Option("--interval")] = 10,
    number: Annotated[int, typer.Option("--number")] = 1,
    parallel: Annotated[int, typer.Option("--parallel")] = 1,
    mode: Annotated[ExecutionMode, typer.Option("--mode")] = ExecutionMode.rolling,
    timeout: Annotated[int, typer.Option("--timeout")] = 600,
    volume_number: Annotated[int, typer.Option("--volume-number")] = 1,
    volume_size: Annotated[int, typer.Option("--volume-size")] = 1,
    cloud_name: Annotated[str, typer.Option("--cloud")] = "simple-stress",
    flavor_name: Annotated[str, typer.Option("--flavor")] = "SCS-1V-2",
    image_name: Annotated[str, typer.Option("--image")] = "Ubuntu 24.04",
    subnet_cidr: Annotated[str, typer.Option("--subnet-cidr")] = "10.100.0.0/16",
    prefix: Annotated[str, typer.Option("--prefix")] = "simple-stress",
    compute_zone: Annotated[str, typer.Option("--compute-zone")] = "nova",
    storage_zone: Annotated[str, typer.Option("--storage-zone")] = "nova",
    affinity: Annotated[
        AffinitySetting, typer.Option("--affinity")
    ] = AffinitySetting.soft_anti,
    volume_type: Annotated[str, typer.Option("--volume-type")] = "__DEFAULT__",
    boot_volume_size: Annotated[int, typer.Option("--boot-volume-size")] = 20,
) -> None:
    # Apply profile overrides (CLI flags take precedence over profile values)
    if profile:
        p = load_profile(profile)

        def _apply(yaml_key, current):
            if yaml_key not in p:
                return current
            param_name = PROFILE_KEY_TO_PARAM.get(yaml_key, yaml_key)
            source = ctx.get_parameter_source(param_name)
            if source == click.core.ParameterSource.DEFAULT:
                return p[yaml_key]
            return current

        clean = _apply("clean", clean)
        no_cleanup = _apply("no_cleanup", no_cleanup)
        debug = _apply("debug", debug)
        no_delete = _apply("no_delete", no_delete)
        volume = _apply("volume", volume)
        no_volume = _apply("no_volume", no_volume)
        no_boot_volume = _apply("no_boot_volume", no_boot_volume)
        no_wait = _apply("no_wait", no_wait)
        interval = _apply("interval", interval)
        number = _apply("number", number)
        parallel = _apply("parallel", parallel)
        mode = _apply("mode", mode)
        timeout = _apply("timeout", timeout)
        volume_number = _apply("volume_number", volume_number)
        volume_size = _apply("volume_size", volume_size)
        cloud_name = _apply("cloud", cloud_name)
        flavor_name = _apply("flavor", flavor_name)
        image_name = _apply("image", image_name)
        subnet_cidr = _apply("subnet_cidr", subnet_cidr)
        prefix = _apply("prefix", prefix)
        compute_zone = _apply("compute_zone", compute_zone)
        storage_zone = _apply("storage_zone", storage_zone)
        affinity = _apply("affinity", affinity)
        volume_type = _apply("volume_type", volume_type)
        boot_volume_size = _apply("boot_volume_size", boot_volume_size)

        # Convert string values from YAML to enums
        if isinstance(mode, str):
            mode = ExecutionMode(mode)
        if isinstance(affinity, str):
            affinity = AffinitySetting(affinity)

    # Clean mode: find and delete leftover resources from a previous run
    if clean:
        clean_resources(cloud_name, prefix, debug)
        return

    # Register signal handler for CTRL+C
    signal.signal(signal.SIGINT, signal_handler)
    delete = not no_delete
    cleanup = not no_cleanup
    meta = Meta(not no_wait, interval, timeout, delete)

    # Handle volume parameters - --no-volume overrides --volume
    if no_volume:
        volume = False

    openstack.enable_logging(debug=debug, http_debug=debug)

    patch_http_connection_pool(maxsize=parallel)
    patch_https_connection_pool(maxsize=parallel)

    user_data = """
    #cloud-config
    final_message: "The system is finally up, after $UPTIME seconds"
    """

    b64_user_data = base64.b64encode(user_data.encode("utf-8")).decode("utf-8")

    report = Report()
    report.params = {
        "profile": profile or None,
        "number": number,
        "parallel": parallel,
        "mode": mode.value,
        "flavor": flavor_name,
        "image": image_name,
        "volume_number": volume_number,
        "volume_size": volume_size,
        "volume_type": volume_type,
        "boot_from_volume": not no_boot_volume,
        "boot_volume_size": boot_volume_size,
        "cloud": cloud_name,
        "affinity": affinity.value,
        "delete": delete,
        "cleanup": cleanup,
    }

    cloud = Cloud(cloud_name, flavor_name, image_name)

    network = cloud.os_cloud.network.find_network(prefix)
    network_created = False
    if network:
        logger.info(f"Using existing network {prefix}")
    else:
        logger.info(f"Creating network {prefix}")
        with report.track("network_create", prefix):
            network = cloud.os_cloud.network.create_network(name=prefix)
        network_created = True

    subnet_name = f"{prefix}-subnet"
    subnet = cloud.os_cloud.network.find_subnet(subnet_name)
    subnet_created = False
    if subnet:
        logger.info(f"Using existing subnet {subnet_name}")
    else:
        logger.info(f"Creating subnet {subnet_name}")
        try:
            ipaddress.ip_network(subnet_cidr)
        except ValueError:
            logger.error(f"Invalid subnet-cidr '{subnet_cidr}'. Using fallback...")
            subnet_cidr = "10.100.0.0/16"

        with report.track("subnet_create", subnet_name):
            subnet = cloud.os_cloud.network.create_subnet(
                name=subnet_name,
                network_id=network.id,
                ip_version="4",
                cidr=subnet_cidr,
            )
        subnet_created = True

    server_group = cloud.os_cloud.compute.find_server_group(prefix)
    server_group_created = False
    if server_group:
        logger.info(f"Using existing server group {prefix}")
    else:
        logger.info(f"Creating server group {prefix}")
        with report.track("server_group_create", prefix):
            server_group = cloud.os_cloud.compute.create_server_group(
                name=prefix, policies=[affinity.value]
            )
        server_group_created = True

    completed_instances = []

    def _submit_create(pool, server_index):
        return pool.submit(
            create,
            cloud,
            f"{prefix}-{server_index}",
            b64_user_data,
            compute_zone,
            volume,
            volume_number,
            storage_zone,
            volume_size,
            server_group,
            volume_type,
            network,
            meta,
            boot_volume_size,
            not no_boot_volume,
            report,
        )

    if mode == ExecutionMode.block:
        total_blocks = -(-number // parallel)
        pool = ThreadPoolExecutor(max_workers=parallel)
        for block_idx in range(total_blocks):
            if shutdown_requested:
                logger.warning("Shutdown requested - skipping remaining blocks...")
                break

            start = block_idx * parallel
            end = min(start + parallel, number)
            block_size = end - start

            logger.info(
                f"Starting block {block_idx + 1}/{total_blocks}"
                f" (servers {start}-{end - 1}, count: {block_size})"
            )

            futures_create = []
            for x in range(start, end):
                futures_create.append(_submit_create(pool, x))

            block_aborted = False
            for future in as_completed(futures_create):
                if shutdown_requested:
                    logger.warning("Shutdown requested - aborting current block...")
                    for f in futures_create:
                        if not f.done():
                            f.cancel()
                    block_aborted = True
                    break

                try:
                    instance = future.result()
                    completed_instances.append(instance)
                    logger.info(f"Server {instance.server.id} finished")
                except Exception as e:
                    logger.error(f"Error creating server: {e}")

            if block_aborted:
                logger.info(f"Block {block_idx + 1}/{total_blocks} aborted")
            else:
                logger.info(f"Block {block_idx + 1}/{total_blocks} completed")
        pool.shutdown(wait=True)
    else:
        pool = ThreadPoolExecutor(max_workers=parallel)
        futures_create = []
        for x in range(number):
            futures_create.append(_submit_create(pool, x))

        # Process completed futures, check for shutdown requests
        for future in as_completed(futures_create):
            if shutdown_requested:
                logger.warning("Shutdown requested - aborting current iteration...")
                break

            try:
                instance = future.result()
                completed_instances.append(instance)
                logger.info(f"Server {instance.server.id} finished")
            except Exception as e:
                logger.error(f"Error creating server: {e}")

        # Cancel remaining futures if shutdown was requested
        if shutdown_requested:
            logger.info("Stopping remaining operations...")
            for future in futures_create:
                if not future.done():
                    future.cancel()

        pool.shutdown(wait=True)

    # Always perform cleanup, even if shutdown was requested
    logger.info("Performing cleanup...")
    futures_delete = []
    cleanup_pool = ThreadPoolExecutor(max_workers=parallel)
    for instance in completed_instances:
        if cleanup and not delete:
            futures_delete.append(
                cleanup_pool.submit(delete_server, instance, meta, report)
            )

    # Wait for deletion to complete
    for f in as_completed(futures_delete):
        try:
            f.result()
        except Exception as e:
            logger.error(f"Error deleting resources: {e}")
    cleanup_pool.shutdown(wait=True)

    # Ensure all volumes are cleaned up, especially if shutdown was requested
    if shutdown_requested or (cleanup and not delete):
        logger.info("Ensuring all volumes are deleted...")
        for instance in completed_instances:
            for vol in instance.volumes:
                try:
                    logger.info(f"Checking and deleting volume {vol.id}")
                    existing_volume = cloud.os_cloud.block_storage.get_volume(vol.id)
                    if existing_volume:
                        with report.track("volume_delete", f"cleanup-{vol.id}"):
                            cloud.os_cloud.block_storage.delete_volume(vol)
                            logger.info(f"Waiting for deletion of volume {vol.id}")
                            cloud.os_cloud.block_storage.wait_for_delete(
                                vol, interval=meta.interval, wait=meta.timeout
                            )
                except Exception as e:
                    logger.error(f"Error deleting volume {vol.id}: {e}")

    # Always clean up infrastructure resources
    if server_group_created:
        try:
            logger.info(f"Deleting server group {prefix}")
            with report.track("server_group_delete", prefix):
                cloud.os_cloud.compute.delete_server_group(server_group)
        except Exception as e:
            logger.error(f"Error deleting server group: {e}")

    if subnet_created:
        try:
            logger.info(f"Deleting subnet {prefix}-subnet")
            with report.track("subnet_delete", subnet_name):
                cloud.os_cloud.network.delete_subnet(subnet, ignore_missing=False)
        except Exception as e:
            logger.error(f"Error deleting subnet: {e}")

    if network_created:
        try:
            logger.info(f"Deleting network {prefix}")
            with report.track("network_delete", prefix):
                cloud.os_cloud.network.delete_network(network, ignore_missing=False)
        except Exception as e:
            logger.error(f"Error deleting network: {e}")

    report.finalize()
    report.print_report()

    runtime = (report.end_time or time.time()) - report.start_time

    if shutdown_requested:
        logger.info(f"Test was aborted - cleanup completed. Runtime: {runtime:.4f}s")
    else:
        logger.info(f"Test completed successfully. Runtime: {runtime:.4f}s")


def main() -> None:
    typer.run(run)


if __name__ == "__main__":
    main()
