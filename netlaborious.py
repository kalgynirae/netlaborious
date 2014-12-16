"""
usage: netlaborious [--verbose] (batch|upload|clone) [options]
       netlaborious --help

Commands:
  batch                 Read lines of arguments from stdin. Each line
                        corresponds to a single invocation of this script.
  check                 Check whether the existing VMs have unique names.
  clone                 Clone an existing VM to another host (TODO: to *all*
                        other hosts).
  info                  Print detailed information about a VM.
  upload                Upload an OVF template to a particular host and make
                        a snapshot of it.

Common options:
    --vshost HOST       vSphere host (default: localhost)
    --vsport PORT       vSphere port (default: 443)
    --vsuser USER       vSphere username
"""
from __future__ import print_function
import contextlib
import getpass
import inspect
import logging
import operator
import pprint
import shlex
import sys

import pysphere
import pyVim.connect
import pyVmomi


_COMMANDS = {}
_NO_ARG_OPTIONS = ['--help', '--verbose']

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
        print(__doc__, end='', file=sys.stderr)
        return 1
    if '--help' in options:
        print(__doc__, end='', file=sys.stderr)
        return 0
    if '--verbose' in options:
        logger.setLevel(logging.DEBUG)
    logger.debug('debug-level logging is enabled')

    # Decide whether to read from stdin
    batch_mode = command == 'batch'
    if batch_mode:
        commands = []
        errors = False
        for n, line in enumerate(sys.stdin, start=1):
            try:
                words = shlex.split(line, comments=True)
            except ValueError as e:
                logger.error('[line %s] %s', n, e)
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
            logger.error('%sinvalid command %r', maybe_line, command)
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

        kwargs = {}
        for option in command_func._optional_options:
            arg_name = option.lstrip('-').replace('-', '_')
            if option in persistent_options_copy:
                kwargs[arg_name] = persistent_options_copy[option]

        funcs.append((lambda cf=command_func, v=values, k=kwargs: cf(*v, **k),
                      line))
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
    global __doc__
    args, _, _, defaults = inspect.getargspec(func)
    options = ['--' + arg.replace('_', '-') for arg in args]
    n = len(args) - len(defaults or [])
    func._required_options = options[:n]
    func._optional_options = options[n:]
    _COMMANDS[func.__name__] = func
    if func.__doc__:
        __doc__ += func.__doc__.rstrip(' ')
    return func


@command
def check(vsuser, vshost=None, vsport=None):
    logger.debug(['check', vsuser, vshost, vsport])
    with pysphere_connection(vshost, vsuser, vsport) as server:
        paths = server.get_registered_vms()
        names = []
        for path in paths:
            name = server.get_vm_by_path(path).get_property('name')
            names.append(name)
            print('name=%r path=%r' % (name, path))
        print('%s names total' % len(names))
        print('%s unique names' % len(set(names)))


@command
def clone(vsuser, src_vm, dest_host, dest_vm, snapshot=None, vshost=None,
          vsport=None):
    """clone options:
    --src-vm NAME       VM to clone
    --dest-host NAME    destination host
    --dest-vm NAME      name of resulting VM
    --snapshot NAME     snapshot to create (no snapshot if absent)
    """
    logger.debug(['clone', vsuser, src_vm, dest_host, dest_vm, snapshot, vshost,
                  vsport])
    with pysphere_connection(vshost, vsuser, vsport) as server:
        logger.debug('Fetching VM %r', src_vm)
        source = server.get_vm_by_name(src_vm)
        source_name = source.get_property('name')

        logger.debug('Creating clone %r', dest_vm)
        clone = source.clone(dest_vm, power_on=False)

        target_host = choose('target host', [server.get_hosts().items()],
                             key=operator.itemgetter(1), choice=dest_host)[0]
        logger.debug('Migrating clone to host %r', target_host)
        clone.migrate(host=target_host)

        if snapshot is not None:
            logger.debug('Creating snapshot %r', snapshot)
            clone.create_snapshot(snapshot)


@command
def info(vsuser, vm, vshost=None, vsport=None):
    """info options:
    --vm NAME           VM about which to print info
    """
    with pysphere_connection(vshost, vsuser, vsport) as server:
        vm = server.get_vm_by_name(vm)
        pprint.pprint(vm.get_properties())
        print('Snapshots:',
              [snapshot.get_name() for snapshot in vm.get_snapshots()])


@command
def snapshot(vsuser, vm, snapshot, vshost=None, vsport=None):
    """snapshot options:
    --vm NAME           VM to snapshot
    --snapshot NAME     snapshot to create
    """
    with pysphere_connection(vshost, vsuser, vsport) as server:
        vm = server.get_vm_by_name(vm)

        # It's possible for multiple snapshots to have the same name, but we
        # want to avoid that scenario.
        existing_snapshot_names = [s.get_name() for s in vm.get_snapshots()]
        existing_same_name_count = existing_snapshot_names.count(snapshot)
        if existing_snapshot_names > 0:
            if ask('%s snapshots named %r exist and will be removed; proceed?' %
                   (existing_same_name_count, snapshot)):
                # Delete all snapshots with that name
                for _ in range(existing_same_name_count):
                    logger.debug('deleting snapshot %r', snapshot)
                    vm.delete_named_snapshot(snapshot)
            else:
                return

        logger.debug('creating snapshot %r', snapshot)
        vm.create_snapshot(snapshot)


@command
def upload(vsuser, ovf, vm, dest_host, dest_folder=None, snapshot=None,
           vshost=None, vsport=None):
    """upload options:
    --ovf PATH          OVF file to upload
    --vm NAME           VM to create
    --dest-host NAME    host on which to create VM
    --dest-folder NAME  folder in which to create VM
    --snapshot NAME     snapshot to create (no snapshot if absent)
    """
    with vsphere_connection(vshost, vsuser, vsport) as conn:
        content = conn.RetrieveContent()

        get_name = operator.attrgetter('name')
        datacenter = choose('datacenter', content.rootFolder.childEntity)
        host = choose('host',
                      [ce.host[0] for ce in datacenter.hostFolder.childEntity],
                      choice=dest_host)
        folder = choose('folder', datacenter.vmFolder.childEntity)

        with open(ovf) as f:
            ovf_descriptor = f.read()
        # Note: each of the following content.ovfManager.<something> calls
        # takes a corresponding <something>Params object. We just use the
        # default versions of those
        parse_descriptor_result = content.ovfManager.ParseDescriptor(
                ovf_descriptor,
                pyVmomi.vim.OvfManager.ParseDescriptorParams())
        validate_host_result = content.ovfManager.ValidateHost(
                ovf_descriptor,
                host,
                pyVmomi.vim.OvfManager.ValidateHostParams())
        create_import_spec_result = content.ovfManager.CreateImportSpec(
                ovf_descriptor,
                resource_pool,
                datastore,
                pyVmomi.vim.OvfManager.CreateImportSpecParams())
        import_spec = create_import_spec_result.importSpec
        http_nfc_lease = resource_pool.ImportVApp(
                import_spec,
                folder,
                host)

        #datacenter = content.rootFolder.childEntity[0]
        #vmfolder = datacenter.vmFolder
        #hosts = datacenter.hostFolder.childEntity
        #resource_pool = hosts[0].resourcePool


@contextlib.contextmanager
def pysphere_connection(host, username, port):
    host = host if host is not None else 'localhost'
    host = '%s:%s' % (host, port) if port is not None else host
    password = get_password(host, username)
    server = pysphere.VIServer()
    server.connect(host, username, password)
    logger.debug('pysphere_connection: connected to vSphere')
    yield server
    server.disconnect()
    logger.debug('pysphere_connection: disconnected from vSphere')


@contextlib.contextmanager
def vsphere_connection(host, username, port):
    host = host if host is not None else 'localhost'
    port = port if port is not None else 443
    password = get_password(host, username)
    service_instance = pyVim.connect.SmartConnect(
            host=host,
            user=username,
            pwd=password,
            port=port)
    logger.debug('vsphere_connection: connected to vSphere')
    yield service_instance
    pyVim.connect.Disconnect(service_instance)
    logger.debug('vsphere_connection: disconnected from vSphere')


def _name_or_repr(obj):
    try:
        return obj.name
    except AttributeError:
        return repr(obj)


def ask(question):
    """Ask question and return True if the user says yes, False otherwise."""
    prompt = '%s (y/N) ' % question
    response = raw_input(prompt)
    return response in ['Y', 'y']


def choose(type, items, key=_name_or_repr, choice=None):
    """Return one item from items, prompting the user to select if necessary.

    If items only contains one item, it is returned immediately.

    key is a function that converts an item into a string representation that
    will be displayed to the user and used to match against choice.

    If choice is specified and an item whose key matches choice is found, that
    item is returned; otherwise, a warning is printed and the user is prompted
    to select an item.
    """
    if len(items) == 1:
        logger.debug('automatically choosing sole %s %r', type, key(items[0]))
        return items[0]
    elif len(items) == 0:
        raise ValueError('no %s available to choose' % type)

    choices = {}
    for n, thing in enumerate(items, start=1):
        k = key(thing)
        if choice is not None and k == choice:
            logger.debug('choosing %s %r', type, choice)
            return thing
        else:
            choices[n] = k

    if choice is not None:
        logger.warning('no such %s %r', type, choice)

    print('Choose a %s:' % type)
    print('\n'.join('  %s %s' % (n, k) for n, k in choices.items()))
    while True:
        try:
            result = items[int(raw_input('Enter a number: ')) - 1]
            break
        except (KeyError, ValueError):
            pass

    logger.debug('chose %s %r', type, key(result))
    return result

def get_password(host, username):
    """Prompt the user to enter the password for username@host.

    If a password has been entered before for this username/host combination,
    the previously-entered password is returned without prompting the user.
    """
    try:
        return get_password._saved[host, username]
    except KeyError:
        password = getpass.getpass(prompt='Enter password for %s@%s: ' %
                                          (username, host))
        get_password._saved[host, username] = password
        return password
get_password._saved = {}


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
