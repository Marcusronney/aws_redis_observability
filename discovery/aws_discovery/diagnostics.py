"""Optional phase markers. Never accept credentials, responses or exception bodies."""
import os
import sys


def phase(name):
    if os.environ.get('DISCOVERY_TRACE_PHASES') == 'true':
        print('PHASE: ' + name, file=sys.stderr, flush=True)
