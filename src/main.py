import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import sys
import time

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

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)
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
    name = "%s-%d" % (CONF.prefix, x)
    server = create_server(name, image, flavor, network, user_data)

    volumes = []
    if CONF.volume:
        for x in range(CONF.volume_number):
            volume = create_volume("%s-volume-%d" % (name, x))
            volumes.append(volume)

        for volume in volumes:
            logging.info(
                "Attaching volume %s to server %s (%s)" % (volume.id, server.id, name)
            )
            cloud.attach_volume(server, volume)

            logging.info("Refreshing details of %s (%s)" % (server.id, name))
            server = cloud.compute.get_server(server.id)

    if CONF.delete:
        delete(server, volumes)
    else:
        logging.info("Skipping deletion of server %s (%s)" % (server.id, name))
        for volume in volumes:
            logging.info(
                "Skipping deletion of volume %s from server %s (%s)"
                % (volume.id, server.id, name)
            )

    return (server, volumes)


def create_volume(name):
    logging.info("Creating volume %s" % name)

    volume = cloud.block_storage.create_volume(
        availability_zone=CONF.storage_zone, name=name, size=CONF.volume_size
    )

    logging.info("Waiting for volume %s" % volume.id)
    cloud.block_storage.wait_for_status(
        volume, status="available", interval=CONF.interval, wait=CONF.timeout
    )

    return volume


def create_server(name, image, flavor, network, user_data):
    logging.info("Creating server %s" % name)

    server = cloud.compute.create_server(
        availability_zone=CONF.compute_zone,
        name=name,
        image_id=image.id,
        flavor_id=flavor.id,
        networks=[{"uuid": network.id}],
        user_data=user_data,
    )

    logging.info("Waiting for server %s (%s)" % (server.id, name))
    cloud.compute.wait_for_server(server, interval=CONF.interval, wait=CONF.timeout)

    if CONF.wait:
        logging.info("Waiting for boot / test results of %s (%s)" % (server.id, name))
        while True:
            console = cloud.compute.get_server_console_output(server)
            if "Failed to run module scripts-user" in str(console):
                logging.error("Failed tests for %s (%s)" % (server.id, name))
            if "The system is finally up" in str(console):
                break
            time.sleep(1.0)

    return server


def delete(server, volumes):
    logging.info("Deleting server %s (%s)" % (server.id, server.name))
    cloud.compute.delete_server(server)

    logging.info("Waiting for deletion of server %s (%s)" % (server.id, server.name))
    cloud.compute.wait_for_delete(server, interval=CONF.interval, wait=CONF.timeout)

    for volume in volumes:
        logging.info(
            "Deleting volume %s from server %s (%s)"
            % (volume.id, server.id, server.name)
        )
        cloud.block_storage.delete_volume(volume)

        logging.info("Waiting for deletion of volume %s" % volume.id)
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

logging.info("Checking flavor %s" % CONF.flavor)
flavor = cloud.get_flavor(CONF.flavor)
logging.info("flavor.id = %s" % flavor.id)

logging.info("Checking image %s" % CONF.image)
image = cloud.get_image(CONF.image)
logging.info("image.id = %s" % image.id)

logging.info("Checking network %s" % CONF.network)
network = cloud.get_network(CONF.network)
logging.info("network.id = %s" % network.id)

start = time.time()

pool = ThreadPoolExecutor(max_workers=CONF.parallel)
futures_create = []
for x in range(CONF.number):
    futures_create.append(pool.submit(create, x, image, flavor, network, b64_user_data))

futures_delete = []
for server, volumes in [x.result() for x in as_completed(futures_create)]:
    logging.info("Server %s finished" % server.id)

    if CONF.cleanup and not CONF.delete:
        futures_delete.append(pool.submit(delete, server, volumes))

for x in as_completed(futures_delete):
    pass

end = time.time()

logging.info(f"Runtime: {(end-start):.4f}s")
