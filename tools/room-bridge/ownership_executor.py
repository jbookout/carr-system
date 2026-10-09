"""Report the child's kernel identity and await durable ownership before exec."""
import json
import os
import sys

import write_ownership


def main():
    receipt, gate = map(int, sys.argv[1:3])
    dispatching = sys.argv[3] == '--dispatch'
    request = json.load(sys.stdin) if dispatching else None
    identity = {**write_ownership.process_owner(), 'kind': 'launch_gate' if dispatching else 'process_group',
                'pgid': os.getpgrp()}
    if dispatching:
        def report(value):
            data = (json.dumps(value) + '\n').encode()
            while data:
                data = data[os.write(receipt, data):]

        def bind(identity):
            report({'type': 'executor', 'identity': identity})
            if os.read(gate, 1) != b'1':
                raise SystemExit(125)

        bind(identity)
        # Persist uncertainty before an adapter can hand work to a remote process.
        # A proxy's exit does not prove that an external writer terminated.
        bind({**identity, 'kind': 'unconfirmed'})
        try:
            import dispatch
            outcome = dispatch._execute(request, on_executor=bind)
            report({'type': 'result', 'outcome': outcome})
        except Exception as exc:
            report({'type': 'error', 'detail': str(exc), 'code': getattr(exc, 'code', None)})
        finally:
            os.close(receipt)
            os.close(gate)
        return
    os.write(receipt, (json.dumps(identity) + '\n').encode())
    os.close(receipt)
    authorized = os.read(gate, 1) == b'1'
    os.close(gate)
    if authorized and identity['start_time']:
        os.execvpe(sys.argv[3], sys.argv[3:], os.environ)
    raise SystemExit(125)


if __name__ == '__main__':
    main()
