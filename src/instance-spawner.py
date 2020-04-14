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
    cfg.BoolOpt('delete', default=True),
    cfg.BoolOpt('floating', default=False),
    cfg.BoolOpt('test', default=True),
    cfg.BoolOpt('volume', default=False),
    cfg.IntOpt('number', default=1),
    cfg.IntOpt('parallel', default=1),
    cfg.IntOpt('timeout', default=600),
    cfg.IntOpt('volume-number', default=2),
    cfg.IntOpt('volume-size', default=1),
    cfg.StrOpt('cloud', help='Cloud name in clouds.yaml', default='testbed'),
    cfg.StrOpt('flavor', default='1C-1GB-10GB'),
    cfg.StrOpt('image', default='Ubuntu 18.04'),
    cfg.StrOpt('keypair'),
    cfg.StrOpt('network', default='net-to-external-testbed'),
    cfg.StrOpt('prefix', default='test'),
    cfg.StrOpt('zone', help='Availability zone to use', default='south-2'),
]

CONF.register_cli_opts(opts)
CONF(sys.argv[1:], project=PROJECT_NAME)

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO, datefmt='%Y-%m-%d %H:%M:%S')


def run(x, image, flavor, network, user_data):
    name = "%s-%d" % (CONF.prefix, x)
    logging.info("Creating server %s" % name)

    server = cloud.compute.create_server(
        availability_zone=CONF.zone,
        name=name, image_id=image.id, flavor_id=flavor.id,
        networks=[{"uuid": network.id}], user_data=user_data)

    logging.info("Waiting for server %s (%s)" % (server.id, name))
    cloud.compute.wait_for_server(server, interval=5, wait=CONF.timeout)

    logging.info("Waiting for boot / test results of %s (%s)" % (server.id, name))
    while True:
        console = cloud.compute.get_server_console_output(server)
        if "Failed to run module scripts-user" in str(console):
            logging.error("Failed tests for %s (%s)" % (server.id, name))
        if "The system is finally up" in str(console):
            break
        time.sleep(5.0)

    volumes = []
    if CONF.volume:
        for x in range(CONF.volume_number):
            volume_name = "%s-volume-%d" % (name, x)

            logging.info("Creating volume %s for server %s (%s)" % (volume_name, server.id, name))
            volume = cloud.block_storage.create_volume(
                availability_zone=CONF.zone,
                name=volume_name, size=CONF.volume_size
            )

            logging.info("Waiting for volume %s" % volume.id)
            cloud.block_storage.wait_for_status(volume, status="available", interval=5, wait=CONF.timeout)

            volumes.append(volume)

        for volume in volumes:
            logging.info("Attaching volume %s to server %s (%s)" % (volume.id, server.id, name))
            cloud.attach_volume(server, volume)

    if CONF.delete:
        logging.info("Deleting server %s (%s)" % (server.id, name))
        cloud.compute.delete_server(server)

        logging.info("Waiting for deletion of server %s (%s)" % (server.id, name))
        cloud.compute.wait_for_delete(server, interval=5, wait=CONF.timeout)

        for volume in volumes:
            logging.info("Deleting volume %s from server %s (%s)" % (volume.id, server.id, name))
            cloud.block_storage.delete_volume(volume)

            logging.info("Waiting for deletion of volume %s" % volume.id)
            cloud.block_storage.wait_for_delete(volume, interval=5, wait=CONF.timeout)
    else:
        logging.info("Skipping deletion of server %s (%s)" % (server.id, name))
        for volume in volumes:
            logging.info("Skipping deletion of volume %s from server %s (%s)" % (volume.id, server.id, name))

    return server.id


cloud = openstack.connect(cloud=CONF.cloud)

if CONF.test:
    user_data_script = """
      ping -c3 $(/sbin/ip route | awk '/default/ { print $3 }') || exit 1
      dd if=/dev/zero of=/tmp/laptop.bin bs=128M count=8 oflag=direct
      sleep 10
    """
else:
    user_data_script = """
      ping -c3 $(/sbin/ip route | awk '/default/ { print $3 }') || exit 1
    """

user_data = """
#cloud-config
write_files:
  - content: |
      #!/usr/bin/env bash
      {user_data_script}
    path: /root/run.sh
    permissions: 0700
runcmd:
  - "/root/run.sh"
final_message: "The system is finally up, after $UPTIME seconds"
""".format(user_data_script=user_data_script)

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
    futures.append(pool.submit(run, x, image, flavor, network, b64_user_data))

for x in as_completed(futures):
    logging.info("Server %s finished" % x.result())
