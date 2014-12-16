**netlaborious** is a Python tool to ease managing vSphere and NETLAB+ setups.

# Setup

Create a virtualenv and install dependencies:

    $ virtualenv env
    $ env/bin/pip install -r requirements.txt

Then run the script using the virtualenv Python:

    $ env/bin/python netlaborious.py

# Usage

    $ netlaborious.py [--verbose] <command> [options]

## Batch mode

If `<command>` is `batch`, netlaborious will read lines of `<command> [options]`
from stdin.  This is done using Python's `shlex` module, so normal shell-style
word splitting is performed, and lines starting with `#` are ignored.  If a line
begins with the special command `ARGS`, the options specified on that line will
be applied to every following command (until the next `ARGS` line).

# Current status of commands:

*   `batch` – complete.

*   `clone` – complete, but I get an error (`Unable to access file [datastore1]
    New Virtual Machine-7/New Virtual Machine-7.vmx`). It's possible the code is
    doing something incorrect, but I instead suspect that my account is missing
    a needed permission.

*   `info` – complete.

*   `snapshot` – complete.

*   `upload` – almost complete; the code to do HTTP uploads still needs to be
    written.
