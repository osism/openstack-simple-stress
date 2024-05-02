# SPDX-License-Identifier: AGPL-3.0-or-later

import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
import sys
import time

from loguru import logger
import openstack
import typer

from typing import List, Tuple

log_fmt = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | "
    "<level>{message}</level>"
)

logger.remove()
logger.add(sys.stderr, format=log_fmt, level="INFO", colorize=True)


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


def create(
    os_cloud,
    prefix,
    x,
    os_image,
    os_flavor,
    os_network,
    user_data,
    compute_zone,
    interval,
    timeout,
    wait,
    volume,
    volume_number,
    storage_zone,
    volume_size,
    delete,
) -> Tuple[
    openstack.compute.v2.server.Server, List[openstack.block_storage.v2.volume.Volume]
]:
    name = f"{prefix}-{x}"
    server = create_server(
        os_cloud,
        name,
        os_image,
        os_flavor,
        os_network,
        user_data,
        compute_zone,
        interval,
        timeout,
        wait,
    )

    volumes = []
    if volume:
        for x in range(volume_number):
            volume = create_volume(
                os_cloud,
                f"{name}-volume-{x}",
                storage_zone,
                volume_size,
                interval,
                timeout,
            )
            volumes.append(volume)

        for volume in volumes:
            logger.info(f"Attaching volume {volume.id} to server {server.id} ({name})")
            os_cloud.attach_volume(server, volume)

            logger.info(f"Refreshing details of {server.id} ({name})")
            server = os_cloud.compute.get_server(server.id)

    if delete:
        delete_server(os_cloud, server, volumes, interval, timeout)
    else:
        logger.info(f"Skipping deletion of server {server.id} ({name})")
        for volume in volumes:
            logger.info(
                f"Skipping deletion of volume {volume.id} from server {server.id} ({name})"
            )

    return (server, volumes)


def create_volume(
    os_cloud, name, storage_zone, volume_size, interval, timeout
) -> openstack.block_storage.v2.volume.Volume:
    logger.info(f"Creating volume {name}")

    volume = os_cloud.block_storage.create_volume(
        availability_zone=storage_zone, name=name, size=volume_size
    )

    logger.info(f"Waiting for volume {volume.id}")
    os_cloud.block_storage.wait_for_status(
        volume, status="available", interval=interval, wait=timeout
    )

    return volume


def create_server(
    os_cloud,
    name,
    os_image,
    os_flavor,
    os_network,
    user_data,
    compute_zone,
    interval,
    timeout,
    wait,
) -> openstack.compute.v2.server.Server:
    logger.info(f"Creating server {name}")

    server = os_cloud.compute.create_server(
        availability_zone=compute_zone,
        name=name,
        image_id=os_image.id,
        flavor_id=os_flavor.id,
        networks=[{"uuid": os_network.id}],
        user_data=user_data,
    )

    logger.info(f"Waiting for server {server.id} ({name})")
    os_cloud.compute.wait_for_server(server, interval=interval, wait=timeout)

    if wait:
        logger.info(f"Waiting for boot / test results of {server.id} ({name})")
        while True:
            console = os_cloud.compute.get_server_console_output(server)
            if "Failed to run module scripts-user" in str(console):
                logger.error(f"Failed tests for {server.id} ({name})")
            if "The system is finally up" in str(console):
                break
            time.sleep(1.0)

    return server


def delete_server(os_cloud, server, volumes, interval, timeout) -> None:
    logger.info(f"Deleting server {server.id} ({server.name})")
    os_cloud.compute.delete_server(server)

    logger.info(f"Waiting for deletion of server {server.id} ({server.name})")
    os_cloud.compute.wait_for_delete(server, interval=interval, wait=timeout)

    for volume in volumes:
        logger.info(
            f"Deleting volume {volume.id} from server {server.id} ({server.name})"
        )
        os_cloud.block_storage.delete_volume(volume)

        logger.info(f"Waiting for deletion of volume {volume.id}")
        os_cloud.block_storage.wait_for_delete(volume, interval=interval, wait=timeout)


def run(
    cleanup: bool = typer.Option(True, "--cleanup"),
    debug: bool = typer.Option(False, "--debug"),
    delete: bool = typer.Option(True, "--delete"),
    floating: bool = typer.Option(False, "--floating"),
    volume: bool = typer.Option(False, "--volume"),
    wait: bool = typer.Option(True, "--wait"),
    interval: int = typer.Option(10, "--interval"),
    number: int = typer.Option(1, "--number"),
    parallel: int = typer.Option(1, "--parallel"),
    timeout: int = typer.Option(600, "--timeout"),
    volume_number: int = typer.Option(1, "--volume-number"),
    volume_size: int = typer.Option(1, "--volume-size"),
    cloud: str = typer.Option("simple-stress", "--cloud", help="Cloud name"),
    flavor: str = typer.Option("SCS-1V-1-10", "--flavor"),
    image: str = typer.Option("Ubuntu 22.04", "--image"),
    keypair: str = typer.Option(None, "--keypair"),
    network: str = typer.Option("simple-stress", "--network"),
    prefix: str = typer.Option("simple-stress", "--prefix"),
    compute_zone: str = typer.Option(
        "nova", "--compute-zone", help="Compute availability zone to use"
    ),
    network_zone: str = typer.Option(
        "nova", "--network-zone", help="Network availability zone to use"
    ),
    storage_zone: str = typer.Option(
        "nova", "--storage-zone", help="Storage availability zone to use"
    ),
) -> None:
    openstack.enable_logging(debug=debug, http_debug=debug)

    patch_http_connection_pool(maxsize=parallel)
    patch_https_connection_pool(maxsize=parallel)

    os_cloud = openstack.connect(cloud=cloud)

    user_data = """
    #cloud-config
    final_message: "The system is finally up, after $UPTIME seconds"
    """

    b64_user_data = base64.b64encode(user_data.encode("utf-8")).decode("utf-8")

    logger.info(f"Checking flavor {flavor}")
    os_flavor = os_cloud.get_flavor(flavor)
    logger.info(f"flavor.id = {os_flavor.id}")

    logger.info(f"Checking image {image}")
    os_image = os_cloud.get_image(image)
    logger.info(f"image.id = {os_image.id}")

    logger.info(f"Checking network {network}")
    os_network = os_cloud.get_network(network)
    logger.info(f"network.id = {os_network.id}")

    start = time.time()

    pool = ThreadPoolExecutor(max_workers=parallel)
    futures_create = []
    for x in range(number):
        futures_create.append(
            pool.submit(
                create,
                os_cloud,
                prefix,
                x,
                os_image,
                os_flavor,
                os_network,
                b64_user_data,
                compute_zone,
                interval,
                timeout,
                wait,
                volume,
                volume_number,
                storage_zone,
                volume_size,
                delete,
            )
        )

    futures_delete = []
    for server, volumes in [x.result() for x in as_completed(futures_create)]:
        logger.info(f"Server {server.id} finished")

        if cleanup and not delete:
            futures_delete.append(
                pool.submit(delete_server, os_cloud, server, volumes, interval, timeout)
            )

    for f in as_completed(futures_delete):
        pass

    end = time.time()

    logger.info(f"Runtime: {(end-start):.4f}s")


def main() -> None:
    typer.run(run)


if __name__ == "__main__":
    main()
