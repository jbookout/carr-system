import json
from pathlib import Path
from lib.rule_delivery_precision import select as propose

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = json.loads((Path(__file__).parent / 'baseline-contract.json').read_text())
CONFIG = json.loads((Path(__file__).parent / 'candidate-config.json').read_text())


def select(payload, baseline_ids):
    return propose(ROOT, payload, baseline_ids, CONTRACT['boot_ids'],
                   {**CONFIG, 'active_ids': CONTRACT['active_ids']})
