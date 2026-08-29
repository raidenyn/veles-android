function tokenize(expression) {
  if (typeof expression !== 'string' || expression.trim() === '') return null;

  const tokens = expression.match(/[A-Za-z0-9.+-]+|\(|\)|AND|OR/g);
  if (!tokens || tokens.join(' ') !== expression.trim().replace(/\s+/g, ' ')) return null;
  return tokens;
}

function parseExpression(tokens) {
  let index = 0;

  function parsePrimary() {
    const token = tokens[index++];
    if (token === '(') {
      const node = parseOr();
      if (tokens[index++] !== ')') return null;
      return node;
    }
    if (!token || token === 'AND' || token === 'OR' || token === ')') return null;
    if (tokens[index] === 'WITH') {
      const exception = tokens[index + 1];
      if (!exception || exception === 'AND' || exception === 'OR' || exception === ')') return null;
      index += 2;
      return { type: 'license', value: `${token} WITH ${exception}` };
    }
    return { type: 'license', value: token };
  }

  function parseAnd() {
    let node = parsePrimary();
    while (node && tokens[index] === 'AND') {
      index += 1;
      const right = parsePrimary();
      if (!right) return null;
      node = { type: 'and', left: node, right };
    }
    return node;
  }

  function parseOr() {
    let node = parseAnd();
    while (node && tokens[index] === 'OR') {
      index += 1;
      const right = parseAnd();
      if (!right) return null;
      node = { type: 'or', left: node, right };
    }
    return node;
  }

  const tree = parseOr();
  return tree && index === tokens.length ? tree : null;
}

function evaluate(node, allowed) {
  if (node.type === 'license') {
    return allowed.has(node.value) ? { allowed: true, selectedLicense: node.value } : { allowed: false };
  }
  if (node.type === 'and') {
    const left = evaluate(node.left, allowed);
    const right = evaluate(node.right, allowed);
    return left.allowed && right.allowed ? left : { allowed: false };
  }
  const left = evaluate(node.left, allowed);
  return left.allowed ? left : evaluate(node.right, allowed);
}

function diagnosticFor(expression, policy) {
  const details = policy.diagnostic ?? {};
  const packageVersion = details.package && details.version
    ? `${details.package}@${details.version}`
    : 'unknown package/version';
  const licenseText = details.licenseText ?? details.licensePath ?? 'no license text or local path available';
  return `License policy rejected ${packageVersion}: ${String(expression)}; ` +
    `license evidence: ${licenseText}; policy: ${policy.policyPath ?? '.license-policy.json'}`;
}

export function evaluateNpmLicense(expression, policy) {
  const tree = parseExpression(tokenize(expression) ?? []);
  if (tree) {
    const result = evaluate(tree, new Set(policy.allowed));
    if (result.allowed) return result;
  }
  return {
    allowed: false,
    selectedLicense: null,
    diagnostic: diagnosticFor(expression, policy),
  };
}
