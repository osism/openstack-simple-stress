# openstack-simple-stress

A tool to perform simple load tests on an OpenStack environment. For use by integrators
when setting up new environments or for rapid problem analysis. Rally and Tempest are
used for real tests and regular checks.

## Usage

```
$ tox -- --parallel 2 --number 2 --network test
simple-stress run-test-pre: PYTHONHASHSEED='3782665235'
simple-stress run-test: commands[0] | python3 src/main.py --parallel 2 --number 2 --network test
2022-03-09 19:23:50 - INFO - Checking flavor SCS-1L:1:5
2022-03-09 19:23:51 - INFO - flavor.id = 596b4231-ddc0-4271-b678-3d97ec1d7c14
2022-03-09 19:23:51 - INFO - Checking image Ubuntu 20.04
2022-03-09 19:23:51 - INFO - image.id = c62cee00-838b-4de7-a0bc-adbda6d52d6f
2022-03-09 19:23:51 - INFO - Checking network test
2022-03-09 19:23:51 - INFO - network.id = cf9d9140-119e-4e02-9f57-bde70dc6875b
2022-03-09 19:23:51 - INFO - Creating server simple-stress-0
2022-03-09 19:23:51 - INFO - Creating server simple-stress-1
2022-03-09 19:23:52 - INFO - Waiting for server b02d24be-9d94-4cf5-9f57-87ef00630146 (simple-stress-1)
2022-03-09 19:23:52 - INFO - Waiting for server b70f1b73-f27d-413f-a23f-77cdd484ee09 (simple-stress-0)
2022-03-09 19:24:23 - INFO - Waiting for boot / test results of b02d24be-9d94-4cf5-9f57-87ef00630146 (simple-stress-1)
2022-03-09 19:24:23 - INFO - Waiting for boot / test results of b70f1b73-f27d-413f-a23f-77cdd484ee09 (simple-stress-0)
2022-03-09 19:26:06 - INFO - Deleting server b02d24be-9d94-4cf5-9f57-87ef00630146 (simple-stress-1)
2022-03-09 19:26:06 - INFO - Deleting server b70f1b73-f27d-413f-a23f-77cdd484ee09 (simple-stress-0)
2022-03-09 19:26:07 - INFO - Waiting for deletion of server b02d24be-9d94-4cf5-9f57-87ef00630146 (simple-stress-1)
2022-03-09 19:26:07 - INFO - Waiting for deletion of server b70f1b73-f27d-413f-a23f-77cdd484ee09 (simple-stress-0)
2022-03-09 19:26:17 - INFO - Server b02d24be-9d94-4cf5-9f57-87ef00630146 finished
2022-03-09 19:26:17 - INFO - Server b70f1b73-f27d-413f-a23f-77cdd484ee09 finished
__________________________________________________________________ summary __________________________________________________________________
  simple-stress: commands succeeded
  congratulations :)
```
