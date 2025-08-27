# SPDX-License-Identifier: AGPL-3.0-or-later

import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from enum import Enum
import ipaddress
import signal
import sys
import time
from typing import List

from loguru import logger
import openstack
import typer
from typing_extensions import Annotated

log_fmt = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | "
    "<level>{message}</level>"
)

logger.remove()
logger.add(sys.stderr, format=log_fmt, level="INFO", colorize=True)

shutdown_requested = False


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
    ) -> None:
        volume = create_volume(
            self.cloud,
            name,
            storage_zone,
            volume_size,
            volume_type,
            meta,
        )
        self.volumes.append(volume)

    def attach_volumes(self) -> None:
        for volume in self.volumes:
            logger.info(
                f"Attaching volume {volume.id} to server {self.server.id} ({self.server_name})"
            )
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
    )

    if volume:
        for x in range(volume_number):
            instance.add_volume(
                f"{name}-volume-{x}", storage_zone, volume_size, volume_type, meta
            )

    instance.attach_volumes()

    if meta.delete:
        delete_server(instance, meta)
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
) -> openstack.block_storage.v2.volume.Volume:
    logger.info(f"Creating volume {name}")

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
) -> openstack.compute.v2.server.Server:
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

    server = cloud.os_cloud.compute.create_server(
        availability_zone=compute_zone,
        name=name,
        flavor_id=cloud.os_flavor.id,
        networks=[{"uuid": network.id}],
        user_data=user_data,
        scheduler_hints={"group": server_group.id},
        block_device_mapping=block_device_mapping,
    )

    logger.info(f"Waiting for server {server.id} ({name})")
    cloud.os_cloud.compute.wait_for_server(
        server, interval=meta.interval, wait=meta.timeout
    )

    if meta.wait:
        logger.info(f"Waiting for boot of {server.id} ({name})")
        while True:
            console = cloud.os_cloud.compute.get_server_console_output(server)
            if "Failed to run module scripts-user" in str(console):
                logger.error(f"Failed tests for {server.id} ({name})")
            if "The system is finally up" in str(console):
                break
            time.sleep(1.0)

    return server


def delete_server(instance: Instance, meta: Meta) -> None:
    logger.info(f"Deleting server {instance.server.id} ({instance.server.name})")
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


def run(
    no_cleanup: Annotated[bool, typer.Option("--no-cleanup")] = False,
    debug: Annotated[bool, typer.Option("--debug")] = False,
    no_delete: Annotated[bool, typer.Option("--no-delete")] = False,
    volume: Annotated[bool, typer.Option("--volume")] = True,
    no_wait: Annotated[bool, typer.Option("--no-wait")] = False,
    interval: Annotated[int, typer.Option("--interval")] = 10,
    number: Annotated[int, typer.Option("--number")] = 1,
    parallel: Annotated[int, typer.Option("--parallel")] = 1,
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
    # Register signal handler for CTRL+C
    signal.signal(signal.SIGINT, signal_handler)
    delete = not no_delete
    cleanup = not no_cleanup
    meta = Meta(not no_wait, interval, timeout, delete)

    openstack.enable_logging(debug=debug, http_debug=debug)

    patch_http_connection_pool(maxsize=parallel)
    patch_https_connection_pool(maxsize=parallel)

    user_data = """
    #cloud-config
    final_message: "The system is finally up, after $UPTIME seconds"
    """

    b64_user_data = base64.b64encode(user_data.encode("utf-8")).decode("utf-8")

    start = time.time()

    cloud = Cloud(cloud_name, flavor_name, image_name)

    logger.info(f"Creating network {prefix}")
    network = cloud.os_cloud.network.create_network(name=prefix)

    logger.info(f"Creating subnet {prefix}-subnet")
    try:
        ipaddress.ip_network(subnet_cidr)
    except ValueError:
        logger.error(f"Invalid subnet-cidr '{subnet_cidr}'. Using fallback...")
        subnet_cidr = "10.100.0.0/16"

    subnet = cloud.os_cloud.network.create_subnet(
        name=f"{prefix}-subnet",
        network_id=network.id,
        ip_version="4",
        cidr=subnet_cidr,
    )

    logger.info(f"Creating server group {prefix}")
    server_group = cloud.os_cloud.compute.create_server_group(
        name=prefix, policies=[affinity.value]
    )

    pool = ThreadPoolExecutor(max_workers=parallel)
    futures_create = []
    for x in range(number):
        futures_create.append(
            pool.submit(
                create,
                cloud,
                f"{prefix}-{x}",
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
            )
        )

    futures_delete = []
    completed_instances = []

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

    # Always perform cleanup, even if shutdown was requested
    logger.info("Performing cleanup...")
    for instance in completed_instances:
        if cleanup and not delete:
            futures_delete.append(pool.submit(delete_server, instance, meta))

    # Wait for deletion to complete
    for f in as_completed(futures_delete):
        try:
            f.result()
        except Exception as e:
            logger.error(f"Error deleting resources: {e}")

    # Ensure all volumes are cleaned up, especially if shutdown was requested
    if shutdown_requested or (cleanup and not delete):
        logger.info("Ensuring all volumes are deleted...")
        for instance in completed_instances:
            for vol in instance.volumes:
                try:
                    logger.info(f"Checking and deleting volume {vol.id}")
                    existing_volume = cloud.os_cloud.block_storage.get_volume(vol.id)
                    if existing_volume:
                        cloud.os_cloud.block_storage.delete_volume(vol)
                        logger.info(f"Waiting for deletion of volume {vol.id}")
                        cloud.os_cloud.block_storage.wait_for_delete(
                            vol, interval=meta.interval, wait=meta.timeout
                        )
                except Exception as e:
                    logger.error(f"Error deleting volume {vol.id}: {e}")

    # Always clean up infrastructure resources
    try:
        logger.info(f"Deleting server group {prefix}")
        cloud.os_cloud.compute.delete_server_group(server_group)
    except Exception as e:
        logger.error(f"Error deleting server group: {e}")

    try:
        logger.info(f"Deleting subnet {prefix}-subnet")
        cloud.os_cloud.network.delete_subnet(subnet, ignore_missing=False)
    except Exception as e:
        logger.error(f"Error deleting subnet: {e}")

    try:
        logger.info(f"Deleting network {prefix}")
        cloud.os_cloud.network.delete_network(network, ignore_missing=False)
    except Exception as e:
        logger.error(f"Error deleting network: {e}")

    end = time.time()

    if shutdown_requested:
        logger.info(
            f"Test was aborted - cleanup completed. Runtime: {(end-start):.4f}s"
        )
    else:
        logger.info(f"Test completed successfully. Runtime: {(end-start):.4f}s")


def main() -> None:
    typer.run(run)


if __name__ == "__main__":
    main()
