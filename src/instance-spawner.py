import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import sys
import time

import openstack
from oslo_config import cfg

PROJECT_NAME = "openstack-instance-spawner"
CONF = cfg.CONF
opts = [
    cfg.BoolOpt('floating', default=False),
    cfg.IntOpt('number', default=1),
    cfg.IntOpt('parallel', default=1),
    cfg.StrOpt('cloud', help='Cloud name in clouds.yaml', default='testbed'),
    cfg.StrOpt('flavor', default='1C-1GB-10GB'),
    cfg.StrOpt('image', default='Ubuntu 18.04'),
    cfg.StrOpt('keypair'),
    cfg.StrOpt('network', default='net-to-public-testbed'),
    cfg.StrOpt('prefix', default='test'),
    cfg.StrOpt('zone', help='Availability zone to use', default='south-2'),
]

CONF.register_cli_opts(opts)
CONF(sys.argv[1:], project=PROJECT_NAME)

logging.basicConfig(format='%(asctime)s - %(message)s', level=logging.INFO, datefmt='%Y-%m-%d %H:%M:%S')


def run(x, image, flavor, network, user_data):
    name = "%s-%d" % (CONF.prefix, x)
    logging.info("Creating server %s" % name)
    server = cloud.compute.create_server(
        availability_zone=CONF.zone,
        name=name, image_id=image.id, flavor_id=flavor.id,
        networks=[{"uuid": network.id}], user_data=b64_user_data)

    logging.info("Waiting for server %s" % server.id)
    server = cloud.compute.wait_for_server(server)

    logging.info("Waiting for running tests on %s" % server.id)
    while True:
        time.sleep(5.0)
        console = cloud.compute.get_server_console_output(server)
        if "DONE DONE DONE" in str(console):
            break

    logging.info("Deleting server %s" % server.id)
    cloud.compute.delete_server(server)

    return server.id


cloud = openstack.connect(cloud=CONF.cloud)

user_data = """
#cloud-config
runcmd:
 - [ sh, -xc, "dd if=/dev/zero of=/tmp/laptop.bin bs=128M count=8 oflag=direct" ]
 - [ sh, -xc, "sleep 10" ]
 - [ sh, -xc, "echo $(date) ': DONE DONE DONE'" ]
"""
b64_user_data = base64.b64encode(user_data.encode('utf-8')).decode('utf-8')

logging.info("Checking flavor %s" % CONF.flavor)
flavor = cloud.get_flavor(CONF.flavor)
logging.info("flavor.id = %s" % flavor.id)

logging.info("Checking image %s" % CONF.image)
image = cloud.get_image(CONF.image)
logging.info("image.id = %s" % image.id)

logging.info("Checking network %s" % CONF.network)
network = cloud.get_network(CONF.network)
logging.info("network.id = %s" % network.id)

pool = ThreadPoolExecutor(max_workers=CONF.parallel)
futures = []
for x in range(CONF.number):
    futures.append(pool.submit(run, x, image, flavor, network, user_data))

for x in as_completed(futures):
    logging.info("Server %s finished" % x.result())
