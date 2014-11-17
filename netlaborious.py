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

mkpod options:
  --name NAME           name of the pod to create
  --vm VM               which VMs to attach to the pods (specified by name or
                        some other identifier?)

rmpod options:
  --name NAME           name of the pod to remove
"""
import contextlib
import getpass
import logging
import sys

import bs4
import docopt
import requests
import pyVim.connect
import pyVmomi


formatter = logging.Formatter(fmt='[{name}:{levelname}] {message}', style='{')
handler = logging.StreamHandler()
handler.setFormatter(formatter)
logger = logging.getLogger('netlaborious')
logger.addHandler(handler)
logger.setLevel(logging.INFO)
del formatter, handler


def require(*options, command=None):
    missing = [o for o in options if args[o] is None]
    if command and missing:
        logger.info('command "{}" requires option(s) {}'
             .format(command, ', '.join(missing)))
    for option in missing:
        prompt = 'Enter value for option {}: '.format(option)
        args[option] = input(prompt)


@contextlib.contextmanager
def vsphere_connection():
    require('--vsphere-user')
    password = getpass.getpass(prompt='Enter vSphere password for {}: '
                                      .format(args['--vsphere-user']))
    service_instance = pyVim.connect.SmartConnect(
            host=args['--vsphere-host'],
            user=args['--vsphere-user'],
            pwd=password,
            port=int(args['--vsphere-port']))
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
    try:
        with vsphere_connection() as conn:
            ...
    except requests.exceptions.ConnectionError as e:
        logger.error(e)
        return 1


def action_clone():
    require('--source-vm')
    ...


def action_mkpod():
    require('--name', '--vm')
    ...


def action_rmpod():
    require('--name')
    ...


if __name__ == '__main__':
    args = docopt.docopt(__doc__)
    command = next(arg for arg in args if args[arg] and not arg.startswith('-'))
    logger.debug("command is {!r}".format(command))
    command_func = globals()['action_{}'.format(command)]
    sys.exit(command_func())
