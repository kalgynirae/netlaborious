#!/usr/bin/python
"""
usage: netlaborious [--verbose] [--dry-run]
                    (batch|upload|clone|mkpod|rmpod) [options]
       netlaborious --help

Commands:
  batch                 Read lines of arguments from stdin. Each line
                        corresponds to a single invocation of this script.
  upload                Upload an OVF template to a particular host and make
                        a snapshot of it.
  clone                 Clone an existing VM to all other hosts (and make
                        snapshots).
  mkpod                 Create a set of NETLAB+ pods and map them to VMs.
  rmpod                 Delete a set of NETLAB+ pods.

Options:
  -v, --verbose         print detailed messages for debugging
  -d, --dry-run         don't actually connect to vsphere
  --vshost HOST         vSphere host [default: localhost]
  --vsport PORT         vSphere port [default: 443]
  --vsuser USER         vSphere username
  --dest-host HOST      destination host
  --dest-folder FOLDER  destination folder [default: TestFolder]
  --network NETWORK     network mapping [default: SAFETY NET]
  --ovf OVF             the OVF template to upload
  --provisioning PROV   provisioning type [default: thin]
  --vm NAME             name of the VM to be created/attached/used
  --snapshot NAME       name of snapshot (if not specified, no snapshot)
  --pod NAME            name of the pod to create/remove
"""
from __future__ import print_function
import contextlib
import functools
import getpass
import logging
import re
import shlex
import sys

import bs4
import docopt
import requests
import pysphere
import pyVim.connect
import pyVmomi


logger = logging.getLogger('netlaborious')
logger.setLevel(logging.INFO)


class DryRunDummy(object):
    """A class that returns itself for any attribute access or call"""
    def __call__(self, *args, **kwargs):
        return self

    def __getattr__(self, name):
        return self


class MissingArgumentsError(Exception):
    pass


def args(*option_names):
    def args_decorator(func):
        @functools.wraps(func)
        def wrapped(args):
            values = []
            missing = []
            for option_name in option_names:
                try:
                    values.append(args['--%s' % option_name])
                except KeyError:
                    missing.append(option_name)
            if missing:
                raise MissingArgumentsError('%s requires option(s) %s' %
                                            (func.__name__, ', '.join(missing)))
            else:
                return func(*values)
        return wrapped
    return args_decorator

@args('vshost vsuser ovf vm dest-host dest-folder network provisioning'.split())
def action_upload(host, user, ovf_path, vm_name, dest_hostname, dest_folder,
                  network, provisioning):
    logger.debug('Uploading OVF {} to host {} in folder {} with network {} and '
                 'provisioning {}'
                 .format(ovf, host, folder, network, provisioning))
    with vsphere_connection(host, username, port) as conn:
        content = conn.RetrieveContent()
        #with open(ovf) as ovf:
        #    result = content.ovfManager.ParseDescriptor(ovf.read(), <pdp>)
        datacenter = content.rootFolder.childEntity[0]
        vmfolder = datacenter.vmFolder
        hosts = datacenter.hostFolder.childEntity
        resource_pool = hosts[0].resourcePool
        print(locals())


@args('vshost vsuser source-vm dest-host snapshot-name'.split())
def action_clone(host, user, source_vm, dest_host, snapshot_name):
    with pysphere_connection(host, user) as server:
        logger.debug('Fetching VM {!r}'.format(source_vm))
        source = server.get_vm_by_name(source_vm)

        clone_name = make_clone_name(source.get_property('name'))
        logger.debug('Creating clone {!r}'.format(clone_name))
        clone = source.clone(clone_name, power_on=False)

        target_host = next(host for host, hostname in server.get_hosts().items()
                                if hostname == dest_host)
        logger.debug('Migrating clone to host {!r}'.format(target_host))
        clone.migrate(host=target_host)

        logger.debug('Creating snapshot {!r}'.format(snapshot_name))
        clone.create_snapshot(snapshot_name)


def action_mkpod(name, vm):
    raise NotImplemented


def action_rmpod():
    raise NotImplemented


def get_password(host, username):
    if get_password._saved[host, username]:
        return get_password._saved[host, username]
    else:
        password = getpass.getpass(prompt='Enter password for {}@{}: '
                                          .format(username, host))
        get_password._saved[host, username] = password
        return password
get_password._saved = {}


def make_clone_name(original_name):
    match = re.match('(.*)-([0-9]+)$', original_name)
    if match:
        base = match.group(1)
        number = int(match.group(2)) + 1
    else:
        base = original_name
        number = 0
    return '{}-{}'.format(base, number)


@contextlib.contextmanager
def pysphere_connection(host, username):
    password = get_password(host, username)
    if not DRY_RUN:
        server = VIServer()
        server.connect(host,
                       username,
                       password)
    else:
        server = DryRunDummy()
    logger.debug('pysphere_connection: connected to vSphere')
    yield server
    if not DRY_RUN:
        server.disconnect()
    logger.debug('pysphere_connection: disconnected from vSphere')


@contextlib.contextmanager
def vsphere_connection(host, username, port):
    password = get_password(host, username)
    if not DRY_RUN:
        try:
            service_instance = pyVim.connect.SmartConnect(
                    host=host,
                    user=username,
                    pwd=password,
                    port=int(port))
        except requests.exceptions.ConnectionError as e:
            raise ConnectionError('Failed to connect to vSphere', e)
    else:
        service_instance = DryRunDummy()
    logger.debug('vsphere_connection: connected to vSphere')
    yield service_instance
    if not DRY_RUN:
        pyVim.connect.Disconnect(service_instance)
    logger.debug('vsphere_connection: disconnected from vSphere')


def get_command(args):
    # Docopt already makes sure that exactly one command is specified, so we
    # just need to grab the first one.
    return next(arg for arg in args if args[arg] and not arg.startswith('-'))


def argvs_from_file(file):
    for n, line in enumerate(file):
        # Allow only the first line to start with "ALL"
        if n == 0 and line.startswith('ALL'):

        yield shlex.split(line)

if __name__ == '__main__':
    # Make logging prettier
    formatter = logging.Formatter(fmt='[%(name)s:%(levelname)s] %(message)s')
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    del formatter, handler

    # Do the things
    arguments = docopt.docopt(__doc__)
    argvs = argvs_from_file(sys.stdin) if arguments['batch'] else [sys.argv[1:]]
    DRY_RUN = arguments['--dry-run']
    if arguments['--verbose']:
        logger.setLevel(logging.DEBUG)
    for argv in argvs:
        args = arguments.copy()
        args.update(docopt.docopt(__doc__, argv=argv))
        command = get_command(args)
        logger.debug('command is {!r}'.format(command))
        command_func = globals()['action_{}'.format(command)]
        sys.exit(command_func(args))
