"""Report the child's kernel identity and await durable ownership before exec."""
import json
import os
import sys

import write_ownership


def main():
    receipt, gate = map(int, sys.argv[1:3])
    identity = {**write_ownership.process_owner(), 'kind': 'process_group',
                'pgid': os.getpgrp()}
    os.write(receipt, (json.dumps(identity) + '\n').encode())
    os.close(receipt)
    authorized = os.read(gate, 1) == b'1'
    os.close(gate)
    if authorized and identity['start_time']:
        os.execvpe(sys.argv[3], sys.argv[3:], os.environ)
    raise SystemExit(125)


if __name__ == '__main__':
    main()
