// CARR MCP server — AMENDMENT 2'S CLOSED CALLABLE, in ONE place.
//
// WHY THIS FILE EXISTS (PR 1013, fourth correction round). Two copies of this
// helper guarded two security-critical export surfaces — identity.js's
// authenticated-identity door and the Gate Zero producer's emit seam — and the
// review found them already drifting: the bodies still matched byte for byte,
// but the clauses each copy documented no longer did, which is the state a
// duplicated primitive is in immediately before the copy that gets hardened
// stops being the copy the forger calls. A shape whose whole job is to close a
// probe is not a shape worth maintaining twice.
//
// WHAT THE SHAPE IS, clause by clause:
//
//   (a) BOUND, NOT BARE. A bound function has no own `prototype` and is not a
//       constructor, so `Reflect.construct` on it throws — and the engine's
//       refusal names `function () { [native code] }` rather than quoting the
//       defining module's own source text back at whoever probed it.
//   (b) An own `Symbol.hasInstance` DATA property answering false WITHOUT
//       reading the left operand. `x instanceof closed` therefore neither walks
//       x's prototype chain nor runs one of its traps; the control the suites
//       use is a Proxy whose `get` and `getPrototypeOf` both throw.
//   (c) FROZEN, so neither (a) nor (b) can be written over afterwards. This is
//       the clause the first shape enumeration found missing.
//
// WHAT IS DELIBERATELY NOT CHANGED. The Gate Zero seam modules — readers,
// stores, rulings, the assurance gate and the internal binding — keep their own
// local definitions. Each of those files is held to being self-contained, and
// gate-zero-seam-readers.v5.test.mjs asserts BY SOURCE that its module holds
// exactly one such definition; folding them in here would replace a proven
// property with an import. This file unifies the two copies that carry no such
// constraint and that the review named.

/** Amendment 2's closed shape, applied to `callable`. */
export function closedCallable(callable) {
  const closed = callable.bind(null);
  Object.defineProperty(closed, Symbol.hasInstance, {
    value: () => false, writable: false, enumerable: false, configurable: false,
  });
  return Object.freeze(closed);
}
