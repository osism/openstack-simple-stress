# openstack-simple-stress

A tool to perform simple load tests on an OpenStack environment. For use by integrators
when setting up new environments or for rapid problem analysis. Rally and Tempest are
used for real tests and regular checks.

## Usage

* Clone Repository
* Install Tox
  (if not already installed)
  ```
  pipenv install tox
  ```
* Show help
  ```
  $ pipenv run tox -- --help
  simple-stress: commands[0]> python3 openstack_simple_stress/main.py --help

   Usage: main.py [OPTIONS]

  ╭─ Options ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
  │ --no-cleanup                                                                                                               │
  │ --debug                                                                                                                    │
  │ --no-delete                                                                                                                │
  │ --volume                                                                                                                   │
  │ --no-wait                                                                                                                  │
  │ --interval             INTEGER                                           [default: 10]                                     │
  │ --number               INTEGER                                           [default: 1]                                      │
  │ --parallel             INTEGER                                           [default: 1]                                      │
  │ --timeout              INTEGER                                           [default: 600]                                    │
  │ --volume-number        INTEGER                                           [default: 1]                                      │
  │ --volume-size          INTEGER                                           [default: 1]                                      │
  │ --cloud                TEXT                                              [default: simple-stress]                          │
  │ --flavor               TEXT                                              [default: SCS-1V-1-10]                            │
  │ --image                TEXT                                              [default: Ubuntu 24.04]                           │
  │ --subnet-cidr          TEXT                                              [default: 10.100.0.0/16]                          │
  │ --prefix               TEXT                                              [default: simple-stress]                          │
  │ --compute-zone         TEXT                                              [default: nova]                                   │
  │ --storage-zone         TEXT                                              [default: nova]                                   │
  │ --affinity             [soft-affinity|soft-anti-affinity|affinity|anti-  [default: soft-anti-affinity]                     │
  │                        affinity]                                                                                           │
  │ --volume-type          TEXT                                              [default: __DEFAULT__]                            │
  │ --help                                                                   Show this message and exit.                       │
  ╰────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯

    simple-stress: OK (0.35=setup[0.03]+cmd[0.32] seconds)
    congratulations :) (0.39 seconds)
  ```

## Example Usage

### Run a stresstest
  ```
  $ tox -- --parallel 4 --number 20 --network test --cloud yolo
  simple-stress run-test-pre: PYTHONHASHSEED='3782665235'
  simple-stress run-test: commands[0] | python3 src/main.py --parallel 4 --number 20 --network test --cloud yolo
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
### Create a number of virtual machines and remove them manually

* Create a dedicated domain/project and configure a access profile (`clouds.yml`, `secure.yml`
* Create virtual machines
  ```
  $ pipenv run tox -- --parallel 10 --number 20 --prefix testvm --volume --no-delete --flavor SCS-1L-1 --cloud yolo
  ```
* Check the status of the created machines
  ```
  $ openstack server list --long
  ```
* Remove the virtual machines
  ```
  $ openstack server list --all-projects -f json --os_cloud yolo | \
    jq -r '.[] | select(.Name | test("^testvm-\\d+")) | .ID'| \
    xargs openstack --os_cloud server delete 
  ```
