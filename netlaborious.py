"""usage: tool.py upload OVF --destination DEST --
"""
import sys

import bs4
import docopt
import pyVmomi

args = docopt.docopt(__doc__)
