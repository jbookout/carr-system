const LIVE = new Set(['SCAC_MUTATION_REGISTRY_VERSION', 'CURRENT_REGISTRY_VERSION']);
const TOKEN = /\/\/[^\n]*|\/\*[\s\S]*?\*\/|`(?:\\[\s\S]|[^`\\])*`|"(?:\\[\s\S]|[^"\\])*"|'(?:\\[\s\S]|[^'\\])*'|[A-Za-z_$][\w$]*|\S/g;

export function liveRegistryPins(source) {
  const tokens = [...source.matchAll(TOKEN)]
    .filter(match => !match[0].startsWith('//') && !match[0].startsWith('/*'));
  const live = new Set(LIVE);
  for (let i = 0; i < tokens.length - 2; i++) {
    if (LIVE.has(tokens[i][0]) && tokens[i + 1][0] === 'as') live.add(tokens[i + 2][0]);
  }
  const isLive = operand => live.has(operand.at(-1)?.[0]);
  const isPinned = operand =>
    (operand.length === 1 && /^['"]scac-mutation-registry\.v\d+['"]$/.test(operand[0][0])) ||
    /^REGISTRY_V\d+_VERSION$/.test(operand.at(-1)?.[0] ?? '');
  const lines = [];
  for (let i = 0; i < tokens.length - 4; i++) {
    if (tokens[i][0] !== 'assert' || tokens[i + 1][0] !== '.' ||
        !['equal', 'strictEqual'].includes(tokens[i + 2][0]) || tokens[i + 3][0] !== '(') continue;
    const operands = [[], []];
    let side = 0;
    let j = i + 4;
    for (; j < tokens.length; j++) {
      const value = tokens[j][0];
      if (value === ',' || value === ')') {
        if (side === 1 || value === ')') break;
        side = 1;
      } else if (value === '(' || value === ';') {
        break;
      } else operands[side].push(tokens[j]);
    }
    if (side !== 1 || ![',', ')'].includes(tokens[j]?.[0])) continue;
    if ((isLive(operands[0]) && isPinned(operands[1])) ||
        (isPinned(operands[0]) && isLive(operands[1]))) {
      lines.push(source.slice(0, tokens[i].index).split('\n').length);
    }
  }
  return lines;
}
