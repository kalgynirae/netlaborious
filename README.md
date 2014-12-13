**netlaborious** is a set of Python tools to ease managing vSphere and NETLAB+
setups.

# Setup

Create a virtualenv and install dependencies:

    $ virtualenv env
    $ env/bin/pip install -r requirements.txt

Then run the script using the virtualenv Python:

    $ env/bin/python netlaborious.py

# Usage

    $ netlaborious [--verbose] [--dry-run] <command> [options]

## Batch mode

If `<command>` is `batch`, netlaborious will read lines of `<command> [options]`
from stdin.  This is done using Python's `shlex` module, so normal shell-style
word splitting is performed.  If a line begins with the special command `ARGS`,
the arguments specified on that line will be applied to every following command
(or until the next `ARGS` line).
