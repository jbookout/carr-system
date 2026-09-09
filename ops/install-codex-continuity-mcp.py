#!/usr/bin/env python3
"""Install the Codex-only MCP credential adapter; never edit Claude settings."""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
DEST = Path.home() / '.config/carr/codex-continuity'
SERVER = 'carr-codex-continuity'
TOOLS = ['codex-checkpoint', 'codex-read-recovery']
FILES = ['codex-continuity-stdio-proxy.mjs', 'local-client-auth.mjs']


def desired_config(node):
    return {'command': node, 'args': [str(DEST / FILES[0])],
            'enabled_tools': TOOLS,
            'tools': {name: {'approval_mode': 'approve'} for name in TOOLS}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    node = shutil.which('node')
    if not node:
        raise RuntimeError('Node runtime unavailable')
    spec = importlib.util.spec_from_file_location('carr_config', ROOT / 'ops/config-as-code.py')
    config = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(config)
    before = config._codex_user_config_layer()
    expected = copy.deepcopy(before['config'])
    servers = expected.setdefault('mcp_servers', {})
    servers[SERVER] = desired_config(node)
    # Remove the two broken duplicate routes in Codex only. All other tools and
    # authentication settings retain their previous values.
    for name in ('carr', 'carr-records'):
        if name in servers:
            disabled = servers[name].setdefault('disabled_tools', [])
            for tool in TOOLS:
                if tool not in disabled:
                    disabled.append(tool)
    if args.apply:
        DEST.mkdir(parents=True, exist_ok=True, mode=0o700)
        for name in FILES:
            source = ROOT / 'mcp-server' / name
            target = DEST / name
            if not target.exists() or target.read_bytes() != source.read_bytes():
                temporary = DEST / (name + '.tmp')
                temporary.write_bytes(source.read_bytes())
                temporary.replace(target)
        edits = [{'keyPath': 'mcp_servers.' + SERVER, 'value': servers[SERVER], 'mergeStrategy': 'replace'}]
        for name in ('carr', 'carr-records'):
            if name in servers:
                edits.append({'keyPath': 'mcp_servers.' + name + '.disabled_tools',
                              'value': servers[name]['disabled_tools'], 'mergeStrategy': 'replace'})
        after = config._write_codex_config_edits(edits, before['version'])
    else:
        after = before
    if after['config'] != expected:
        raise RuntimeError('Codex continuity MCP configuration differs from the expected scoped update')
    for name in FILES:
        if (DEST / name).read_bytes() != (ROOT / 'mcp-server' / name).read_bytes():
            raise RuntimeError('Installed adapter differs from source: ' + name)
    print(json.dumps({'ok': True, 'server': SERVER, 'tools': TOOLS,
                      'credential': 'existing dedicated Codex credential, never copied into configuration',
                      'adapter_sha256': hashlib.sha256((DEST / FILES[0]).read_bytes()).hexdigest()}))


if __name__ == '__main__':
    main()
