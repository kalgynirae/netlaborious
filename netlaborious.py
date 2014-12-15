#!/usr/bin/python
"""
usage: netlaborious [--verbose] (batch|upload|clone) [options]
       netlaborious --help

Commands:
  batch                 Read lines of arguments from stdin. Each line
                        corresponds to a single invocation of this script.
  check                 Check whether the existing VMs have unique names.
  clone                 Clone an existing VM to all other hosts (and make
                        snapshots).
  info                  Print detailed information about a VM.
  upload                Upload an OVF template to a particular host and make
                        a snapshot of it.

Options:
  --verbose             print detailed messages for debugging
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
import getpass
import inspect
import logging
import pprint
import re
import shlex
import sys

import bs4
import pysphere
import pyVim.connect
import pyVmomi


_COMMANDS = {}
_NO_ARG_OPTIONS = ['--verbose']

logger = logging.getLogger('netlaborious')
logger.setLevel(logging.INFO)


class ArgumentParseError(Exception):
    pass


def main():
    # Make logging prettier
    formatter = logging.Formatter(fmt='%(name)s: %(message)s')
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    del formatter, handler

    # Parse initial invocation arguments
    try:
        command, options = parse_args(sys.argv[1:])
    except ArgumentParseError as e:
        logger.error(e)
        print(__doc__, file=sys.stderr)
        return 1
    if '-v' in options or '--verbose' in options:
        logger.setLevel(logging.DEBUG)
    logger.debug('verbose mode enabled')

    # Decide whether to read from stdin
    batch_mode = command == 'batch'
    if batch_mode:
        commands = []
        errors = False
        for n, line in enumerate(sys.stdin, start=1):
            try:
                words = shlex.split(line, comments=True)
            except ValueError as e:
                logger.error('[line %s] %s' % (n, e))
                errors = True
            # Only process if the line wasn't blank (or a comment)
            if words:
                try:
                    commands.append(parse_args(words, lineno=n) + (line,))
                except ArgumentParseError as e:
                    logger.error(e)
                    errors = True
        if errors:
            logger.error('aborting due to errors; no commands were run.')
            return 1
    else:
        commands = [(command, options, None)]

    # Prepare the commands and make sure the required options were provided
    errors = False
    funcs = []
    persistent_options = {}
    for n, (command, options, line) in enumerate(commands, start=1):
        maybe_line = '[line %s] ' % n if batch_mode else ''
        persistent_options_copy = persistent_options.copy()
        persistent_options_copy.update(options)
        if command == 'ARGS':
            persistent_options = options
            continue

        try:
            command_func = _COMMANDS[command]
        except KeyError as e:
            logger.error('%sinvalid command %r' % (maybe_line, command))
            errors = True

        values = []
        missing = []
        for option in command_func._required_options:
            if option in persistent_options_copy:
                values.append(persistent_options_copy[option])
            else:
                missing.append(option)
        if missing:
            logger.error('%scommand %r requires options %s' %
                         (maybe_line, command, missing))
            errors = True

        funcs.append((lambda v=values, cf=command_func: cf(*v), line))
    if errors:
        logger.error('aborting due to errors; no commands were run.')
        return 1

    # Actually execute the commands
    for func, line in funcs:
        if line:
            print(line, file=sys.stderr, end='')
        func()


def command(func):
    """Register func as a command.

    func's arguments should correspond to the names of command-line options
    (with internal hyphens replaced by underscores).
    """
    args, _, _, defaults = inspect.getargspec(func)
    options = ['--' + arg.replace('_', '-') for arg in args]
    n = len(args) - len(defaults or [])
    func._required_options = options[:n]
    func._optional_options = options[n:]
    _COMMANDS[func.__name__] = func
    return func


@command
def check(vshost, vsuser):
    logger.debug(['check', vshost, vsuser])
    with pysphere_connection(vshost, vsuser) as server:
        paths = server.get_registered_vms()
        names = []
        for path in paths:
            name = server.get_vm_by_path(path).get_property('name')
            names.append(name)
            print('name=%r path=%r' % (name, path))
        print('%s names total' % len(names))
        print('%s unique names' % len(set(names)))


@command
def clone(vshost, vsuser, vm, dest_host, snapshot=None):
    logger.debug(['clone', vshost, vsuser, vm, dest_host, snapshot])
    with pysphere_connection(vshost, vsuser) as server:
        logger.debug('Fetching VM {!r}'.format(vm))
        source = server.get_vm_by_name(vm)
        source_name = source.get_property('name')

        clone_name = make_unique_name(source_name)
        logger.debug('Creating clone {!r}'.format(clone_name))
        clone = source.clone(clone_name, power_on=False)

        target_host = next(host for host, hostname in server.get_hosts().items()
                                if hostname == dest_host)
        logger.debug('Migrating clone to host {!r}'.format(target_host))
        clone.migrate(host=target_host)

        logger.debug('Creating snapshot {!r}'.format(snapshot))
        clone.create_snapshot(snapshot)


@command
def info(vshost, vsuser, vm):
    logger.debug(['info', vshost, vsuser, vm])
    with pysphere_connection(vshost, vsuser) as server:
        v = server.get_vm_by_name(vm)
        pprint.pprint(v.get_properties())


@command
def upload(vshost, vsuser, ovf, vm, dest_host, dest_folder=None,
           network=None, provisioning=None, vsport=443):
    logger.debug(['upload', vshost, vsuser, ovf, vm, dest_host, dest_folder,
                  network, provisioning, vsport])
    with vsphere_connection(vshost, vsuser, vsport) as conn:
        content = conn.RetrieveContent()
        params = pyVmomi.vim.OvfManager.ParseDescriptorParams()
        with open(ovf) as f:
            result = content.ovfManager.ParseDescriptor(f.read(), params)
        #datacenter = content.rootFolder.childEntity[0]
        #vmfolder = datacenter.vmFolder
        #hosts = datacenter.hostFolder.childEntity
        #resource_pool = hosts[0].resourcePool


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


def get_password(host, username):
    try:
        return get_password._saved[host, username]
    except KeyError:
        password = getpass.getpass(prompt='Enter password for {}@{}: '
                                          .format(username, host))
        get_password._saved[host, username] = password
        return password
get_password._saved = {}


def make_unique_name(original_name):
    match = re.match('(.*)-([0-9]+)$', original_name)
    if match:
        base = match.group(1)
        number = int(match.group(2)) + 1
    else:
        base = original_name
        number = 0
    return '{}-{}'.format(base, number)


def parse_args(argv, lineno=None):
    """Parse command and options from argv.

    Any words beginning with a hyphen are considered options, and the word
    following an option is interpreted as that option's argument if the option
    is not listed in _NO_ARG_OPTIONS. The first word that is neither an option
    nor an option's argument is considered the command (there must be exactly
    one such word).
    """
    maybe_line = '[line %s] ' % lineno if lineno else ''
    command = None
    options = {}
    words = iter(argv)
    while True:
        try:
            word = next(words)
        except StopIteration:
            break
        if word.startswith('-'):
            try:
                options[word] = None if word in _NO_ARG_OPTIONS else next(words)
            except StopIteration:
                raise ArgumentParseError('%soption %s missing a value' %
                                         (maybe_line, word))
        else:
            if command is None:
                command = word
            else:
                raise ArgumentParseError('%smultiple commands given: %s' %
                                         (maybe_line, [command, word]))
    if command is None:
        raise ArgumentParseError('%smissing required command' % maybe_line)
    return command, options


if __name__ == '__main__':
    sys.exit(main())
