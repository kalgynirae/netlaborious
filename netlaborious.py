#!/usr/bin/python
"""
usage: netlaborious [-v] [-d] (batch|upload|clone) [options]
       netlaborious --help

Commands:
  batch                 Read lines of arguments from stdin. Each line
                        corresponds to a single invocation of this script.
  upload                Upload an OVF template to a particular host and make
                        a snapshot of it.
  clone                 Clone an existing VM to all other hosts (and make
                        snapshots).

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

    def __getitem__(self, key):
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
                s = ', '.join('%s=%s' % (name, value)
                              for name, value in zip(option_names, values))
                logger.debug('calling %s(%s)' % (func.__name__, s))
                return func(*values)
        return wrapped
    return args_decorator


@args(*'vshost vsport vsuser ovf vm dest-host dest-folder network provisioning'
       .split())
def action_upload(host, port, user, ovf_path, vm_name, dest_hostname,
                  dest_folder, network, provisioning):
    with vsphere_connection(host, user, port) as conn:
        content = conn.RetrieveContent()
        params = pyVmomi.vim.OvfManager.ParseDescriptorParams()
        with open(ovf) as f:
            result = content.ovfManager.ParseDescriptor(f.read(), params)
        #datacenter = content.rootFolder.childEntity[0]
        #vmfolder = datacenter.vmFolder
        #hosts = datacenter.hostFolder.childEntity
        #resource_pool = hosts[0].resourcePool


@args(*'vshost vsuser vm dest-host snapshot'.split())
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
    try:
        return get_password._saved[host, username]
    except KeyError:
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
    server = pysphere.VIServer()
    server.connect(host,
                   username,
                   password)
    logger.debug('pysphere_connection: connected to vSphere')
    yield server
    server.disconnect()
    logger.debug('pysphere_connection: disconnected from vSphere')


@contextlib.contextmanager
def vsphere_connection(host, username, port):
    password = get_password(host, username)
    service_instance = pyVim.connect.SmartConnect(
            host=host,
            user=username,
            pwd=password,
            port=int(port))
    logger.debug('vsphere_connection: connected to vSphere')
    yield service_instance
    pyVim.connect.Disconnect(service_instance)
    logger.debug('vsphere_connection: disconnected from vSphere')


def get_command(args):
    # Docopt already makes sure that exactly one command is specified, so we
    # just need to grab the first one.
    return next(arg for arg in args if args[arg] and not arg.startswith('-'))


def argvs_from_file(file):
    global_
    for line in file:
        if line.startswith('ARGS'):
            global_args = shlex.split(line)
        yield shlex.split(line)


def strip_default_values(s):
    """Returns s with docopt-style default values stripped out"""
    return re.sub(r'\[default:.*\]', '', s)


def generate_args(arguments):
    command = next(arg for arg in arguments if arguments[arg] and
                                               not arg.startswith('-'))
    if command == 'batch':
        persistent_args = parse_args(argv=['batch'])
        for line in sys.stdin:
            words = shlex.split(line)
            if words[0] == 'ARGS':
                # Docopt needs a valid command, not 'ARGS'
                words[0] = 'batch'
                persistent_args = parse_args(argv=words)
            else:
                # Replace persistent args with explicitly-specified args from
                # the current line
                line_args = persistent_args.copy()
                for option, value in parse_args(argv=words, use_defaults=False):
                    if value:
                        line_args[option] = value
                yield words[0], line_args
    else:
        yield command, arguments


def parse_args(argv=None, use_defaults=True):
    """Read arguments with docopt but ignore default values in the usage.

    This is useful when we want to merge one set of arguments into another,
    and we don't want the default values in the second set to overwrite
    explicitly-set values in the first.
    """
    usage = __doc__ if use_defaults else strip_default_values(__doc__)
    return docopt.docopt(usage, argv=argv)


if __name__ == '__main__':
    global DRY_RUN
    # Make logging prettier
    formatter = logging.Formatter(fmt='%(name)s: %(message)s')
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    del formatter, handler

    # Read initial invocation arguments
    arguments = parse_args()
    DRY_RUN = arguments['--dry-run']
    if arguments['--verbose']:
        logger.setLevel(logging.DEBUG)

    # Do the things
    for command, args in generate_args(arguments):
        command_func = globals()['action_%s' % command]
        try:
            command_func(args)
        except MissingArgumentsError as e:
            logger.error(e)
            sys.exit(1)
