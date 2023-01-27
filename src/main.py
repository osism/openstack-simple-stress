import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
import sys
import time

from loguru import logger
import openstack
from oslo_config import cfg

PROJECT_NAME = "openstack-simple-stress"
CONF = cfg.CONF
opts = [
    cfg.BoolOpt("cleanup", default=True),
    cfg.BoolOpt("debug", default=False),
    cfg.BoolOpt("delete", default=True),
    cfg.BoolOpt("floating", default=False),
    cfg.BoolOpt("volume", default=False),
    cfg.BoolOpt("wait", default=True),
    cfg.IntOpt("interval", default=10),
    cfg.IntOpt("number", default=1),
    cfg.IntOpt("parallel", default=1),
    cfg.IntOpt("timeout", default=600),
    cfg.IntOpt("volume-number", default=1),
    cfg.IntOpt("volume-size", default=1),
    cfg.StrOpt("cloud", help="Cloud name", default="simple-stress"),
    cfg.StrOpt("flavor", default="SCS-1L:1:5"),
    cfg.StrOpt("image", default="Ubuntu 20.04"),
    cfg.StrOpt("keypair"),
    cfg.StrOpt("network", default="simple-stress"),
    cfg.StrOpt("prefix", default="simple-stress"),
    cfg.StrOpt("compute-zone", help="Compute availability zone to use", default="nova"),
    cfg.StrOpt("network-zone", help="Network availability zone to use", default="nova"),
    cfg.StrOpt("storage-zone", help="Storage availability zone to use", default="nova"),
]

CONF.register_cli_opts(opts)
CONF(sys.argv[1:], project=PROJECT_NAME)

openstack.enable_logging(debug=CONF.debug, http_debug=CONF.debug)


# source: https://stackoverflow.com/questions/18466079/can-i-change-the-connection-pool-size-for-pythons-requests-module  # noqa
def patch_http_connection_pool(**constructor_kwargs):
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
def patch_https_connection_pool(**constructor_kwargs):
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


def create(x, image, flavor, network, user_data):
    name = f"{CONF.prefix}-{x}"
    server = create_server(name, image, flavor, network, user_data)

    volumes = []
    if CONF.volume:
        for x in range(CONF.volume_number):
            volume = create_volume(f"{name}-volume-{x}")
            volumes.append(volume)

        for volume in volumes:
            logger.info(f"Attaching volume {volume.id} to server {server.id} ({name})")
            cloud.attach_volume(server, volume)

            logger.info(f"Refreshing details of {server.id} ({name})")
            server = cloud.compute.get_server(server.id)

    if CONF.delete:
        delete(server, volumes)
    else:
        logger.info(f"Skipping deletion of server {server.id} ({name})")
        for volume in volumes:
            logger.info(
                f"Skipping deletion of volume {volume.id} from server {server.id} ({name})"
            )

    return (server, volumes)


def create_volume(name):
    logger.info(f"Creating volume {name}")

    volume = cloud.block_storage.create_volume(
        availability_zone=CONF.storage_zone, name=name, size=CONF.volume_size
    )

    logger.info(f"Waiting for volume {volume.id}")
    cloud.block_storage.wait_for_status(
        volume, status="available", interval=CONF.interval, wait=CONF.timeout
    )

    return volume


def create_server(name, image, flavor, network, user_data):
    logger.info(f"Creating server {name}")

    server = cloud.compute.create_server(
        availability_zone=CONF.compute_zone,
        name=name,
        image_id=image.id,
        flavor_id=flavor.id,
        networks=[{"uuid": network.id}],
        user_data=user_data,
    )

    logger.info(f"Waiting for server {server.id} ({name})")
    cloud.compute.wait_for_server(server, interval=CONF.interval, wait=CONF.timeout)

    if CONF.wait:
        logger.info(f"Waiting for boot / test results of {server.id} ({name})")
        while True:
            console = cloud.compute.get_server_console_output(server)
            if "Failed to run module scripts-user" in str(console):
                logger.error(f"Failed tests for {server.id} ({name})")
            if "The system is finally up" in str(console):
                break
            time.sleep(1.0)

    return server


def delete(server, volumes):
    logger.info(f"Deleting server {server.id} ({server.name})")
    cloud.compute.delete_server(server)

    logger.info(f"Waiting for deletion of server {server.id} ({server.name})")
    cloud.compute.wait_for_delete(server, interval=CONF.interval, wait=CONF.timeout)

    for volume in volumes:
        logger.info(
            f"Deleting volume {volume.id} from server {server.id} ({server.name})"
        )
        cloud.block_storage.delete_volume(volume)

        logger.info(f"Waiting for deletion of volume {volume.id}")
        cloud.block_storage.wait_for_delete(
            volume, interval=CONF.interval, wait=CONF.timeout
        )


patch_http_connection_pool(maxsize=CONF.parallel)
patch_https_connection_pool(maxsize=CONF.parallel)

cloud = openstack.connect(cloud=CONF.cloud)

user_data = """
#cloud-config
final_message: "The system is finally up, after $UPTIME seconds"
"""

b64_user_data = base64.b64encode(user_data.encode("utf-8")).decode("utf-8")

logger.info(f"Checking flavor {CONF.flavor}")
flavor = cloud.get_flavor(CONF.flavor)
logger.info(f"flavor.id = {flavor.id}")

logger.info(f"Checking image {CONF.image}")
image = cloud.get_image(CONF.image)
logger.info(f"image.id = {image.id}")

logger.info(f"Checking network {CONF.network}")
network = cloud.get_network(CONF.network)
logger.info(f"network.id = {network.id}")

start = time.time()

pool = ThreadPoolExecutor(max_workers=CONF.parallel)
futures_create = []
for x in range(CONF.number):
    futures_create.append(pool.submit(create, x, image, flavor, network, b64_user_data))

futures_delete = []
for server, volumes in [x.result() for x in as_completed(futures_create)]:
    logger.info(f"Server {server.id} finished")

    if CONF.cleanup and not CONF.delete:
        futures_delete.append(pool.submit(delete, server, volumes))

for x in as_completed(futures_delete):
    pass

end = time.time()

logger.info(f"Runtime: {(end-start):.4f}s")
