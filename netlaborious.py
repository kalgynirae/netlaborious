#!/usr/bin/python
"""
usage: netlaborious (upload|clone|mkpod|rmpod) [options]
       netlaborious --help

Commands:
  upload                Upload an OVF template to a particular host and make
                        a snapshot of it.
  clone                 Clone an existing VM to all other hosts (and make
                        snapshots).
  mkpod                 Create a set of NETLAB+ pods and map them to VMs.
  rmpod                 Delete a set of NETLAB+ pods.

General options:
  -v, --verbose         print detailed messages for debugging

vSphere options:
  --vsphere-host HOST   vSphere host [default: localhost]
  --vsphere-port PORT   vSphere port [default: 443]
  --vsphere-user USER   vSphere username

upload options:
  --folder FOLDER       destination folder [default: TestFolder]
  --host HOST           destination host
  --network NETWORK     network mapping [default: SAFETY NET]
  --ovf OVF             the OVF template to upload
  --provisioning PROV   provisioning type [default: thin]

clone options:
  --source-vm VM        the VM to clone (in format 'host/folder/name'???)
  --target-host HOST    destination host for the clone
  --snapshot-name NAME  name of snapshot (if not specified, no snapshot)

mkpod options:
  --name NAME           name of the pod to create
  --vm VM               which VMs to attach to the pods (specified by name or
                        some other identifier?)

rmpod options:
  --name NAME           name of the pod to remove
"""
from __future__ import print_function
import contextlib
import getpass
import logging
import re
import sys

import bs4
import docopt
import requests
import pysphere
import pyVim.connect
import pyVmomi


formatter = logging.Formatter(fmt='[%(name)s:%(levelname)s] %(message)s')
handler = logging.StreamHandler()
handler.setFormatter(formatter)
logger = logging.getLogger('netlaborious')
logger.addHandler(handler)
logger.setLevel(logging.INFO)
del formatter, handler


def require(*options, **kwargs):
    missing = [o for o in options if args[o] is None]
    if kwargs.get('command') and missing:
        logger.info('command "{}" requires option(s) {}'
             .format(kwargs['command'], ', '.join(missing)))
    for option in missing:
        prompt = 'Enter value for option {}: '.format(option)
        args[option] = input(prompt)


def make_clone_name(vm):
    original_name = vm.get_property('name')
    match = re.match('(.*)-([0-9]+)$', original_name)
    if match:
        base = match.group(1)
        number = int(match.group(2)) + 1
    else:
        base = old_name
        number = 0
    return '{}-{}'.format(base, number)


@contextlib.contextmanager
def pysphere_connection():
    require('--vsphere-user')
    password = getpass.getpass(prompt='Enter vSphere password for {}: '
                                      .format(args['--vsphere-user']))
    server = VIServer()
    server.connect(args['--vsphere-host'],
                   args['--vsphere-user'],
                   password)
    logger.debug('connected to vSphere')
    yield server
    server.disconnect()
    logger.debug('disconnected from vSphere')


@contextlib.contextmanager
def vsphere_connection():
    require('--vsphere-user')
    password = getpass.getpass(prompt='Enter vSphere password for {}: '
                                      .format(args['--vsphere-user']))
    try:
        service_instance = pyVim.connect.SmartConnect(
                host=args['--vsphere-host'],
                user=args['--vsphere-user'],
                pwd=password,
                port=int(args['--vsphere-port']))
    except requests.exceptions.ConnectionError as e:
        raise ConnectionError('Failed to connect to vSphere', e)
    logger.debug('connected to vSphere')
    yield service_instance
    pyVim.connect.Disconnect(service_instance)
    logger.debug('disconnected from vSphere')


def action_upload():
    """Upload and clone an OVF to """
    require('--ovf', '--host', command='upload')
    logger.debug('uploading OVF {} to host {} in folder {} with network {} and '
                 'provisioning {}'
                 .format(args['--ovf'], args['--host'], args['--folder'],
                         args['--network'], args['--provisioning']))
    with vsphere_connection() as conn:
        content = conn.RetrieveContent()
        #with open(args['--ovf']) as ovf:
        #    result = content.ovfManager.ParseDescriptor(ovf.read(), <pdp>)
        datacenter = content.rootFolder.childEntity[0]
        vmfolder = datacenter.vmFolder
        hosts = datacenter.hostFolder.childEntity
        resource_pool = hosts[0].resourcePool
        print(locals())


def action_clone():
    require('--source-vm', '--target-host', '--snapshot-name')
    with pysphere_connection() as server:

        logger.debug('Fetching VM {!r}'.format(args['--source-vm']))
        source = server.get_vm_by_name(args['--source-vm'])

        clone_name = make_clone_name(source)
        logger.debug('Creating clone {!r}'.format(clone_name))
        clone = source.clone(clone_name, power_on=False)

        target_host = next(host for host, hostname in server.get_hosts().items()
                                if hostname == args['--target-host'])
        logger.debug('Migrating clone to host {!r}'.format(target_host))
        clone.migrate(host=target_host)

        logger.debug('Creating snapshot {!r}'.format(args['--snapshot-name']))
        clone.create_snapshot(args['--snapshot-name'])


def action_mkpod():
    require('--name', '--vm')
    raise NotImplemented


def action_rmpod():
    require('--name')
    raise NotImplemented


if __name__ == '__main__':
    args = docopt.docopt(__doc__)
    command = next(arg for arg in args if args[arg] and not arg.startswith('-'))
    logger.debug('command is {!r}'.format(command))
    command_func = globals()['action_{}'.format(command)]
    sys.exit(command_func())
