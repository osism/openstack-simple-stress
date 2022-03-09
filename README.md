# openstack-simple-stress

A tool to perform simple load tests on an OpenStack environment. For use by integrators
when setting up new environments or for rapid problem analysis. Rally and Tempest are
used for real tests and regular checks.

## Usage

```
$ tox -- --parallel 2 --number 2
2020-03-24 14:05:56 - Checking flavor 1C-1GB-10GB
2020-03-24 14:06:02 - flavor.id = 30
2020-03-24 14:06:02 - Checking image Ubuntu 20.04
2020-03-24 14:06:04 - image.id = f0e2d7ab-5534-40f4-887f-6c9a43a2a043
2020-03-24 14:06:04 - Checking network net-to-public-testbed
2020-03-24 14:06:04 - network.id = b11ddb33-a084-4c87-89c7-aa4f9a73b173
2020-03-24 14:06:04 - Creating server test-0
2020-03-24 14:06:04 - Creating server test-1
2020-03-24 14:06:08 - Waiting for server 63bd4f65-9d6b-4d43-b216-515d9e7e5504
2020-03-24 14:06:08 - Waiting for server 730b941a-2c47-417d-934e-7f0f248ebaf5
2020-03-24 14:06:51 - Waiting for running tests on 63bd4f65-9d6b-4d43-b216-515d9e7e5504
2020-03-24 14:06:54 - Waiting for running tests on 730b941a-2c47-417d-934e-7f0f248ebaf5
2020-03-24 14:08:28 - Deleting server 63bd4f65-9d6b-4d43-b216-515d9e7e5504
2020-03-24 14:08:29 - Server 63bd4f65-9d6b-4d43-b216-515d9e7e5504 finished
2020-03-24 14:08:30 - Deleting server 730b941a-2c47-417d-934e-7f0f248ebaf5
2020-03-24 14:08:32 - Server 730b941a-2c47-417d-934e-7f0f248ebaf5 finished
```
