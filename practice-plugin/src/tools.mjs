import { z } from 'zod';
import rates from '../data/rates.json' with { type: 'json' };

// Configure the actual informational page before submission; never a contact form.
export const INFORMATIONAL_PAGE = 'https://example.com/carr/practice-search-information';
const NOTICE = 'Educational estimate only; not legal, financial, or design advice.';
const range = z.strictObject({ low: z.number().finite(), high: z.number().finite() });
const practice = z.enum(['dental', 'medical', 'veterinary']);
const money = z.number().finite().nonnegative();
const textList = z.array(z.string());
const common = { limitations: textList, notice: z.literal(NOTICE) };
const spaceInput = z.strictObject({
  practice_type: practice, providers: z.number().int().min(1).max(50),
  operatories: z.number().int().min(0).max(100), exam_rooms: z.number().int().min(0).max(100),
});
const occupancyInput = z.strictObject({
  square_feet: z.number().min(100).max(100000), market: z.literal('mobile_downtown'),
  lease_type: z.enum(['full_service', 'modified_gross', 'triple_net']),
});
const buyInput = z.strictObject({
  square_feet: z.number().min(100).max(100000), annual_lease_rate: z.number().min(0).max(200),
  purchase_price: z.number().min(1000).max(100000000),
  down_payment_percent: z.number().min(0).max(100), interest_rate_percent: z.number().min(0).max(30),
  loan_term_years: z.number().int().min(1).max(40), holding_years: z.number().int().min(1).max(40),
  annual_owner_cost: z.number().min(0).max(10000000),
  closing_cost_percent: z.number().min(0).max(20), sale_cost_percent: z.number().min(0).max(20),
});

const round = n => Math.round(n * 100) / 100;
const scaled = (r, n) => ({ low: round(r.low * n), high: round(r.high * n) });
const summed = rows => ({
  low: round(rows.reduce((sum, r) => sum + r.low, 0)),
  high: round(rows.reduce((sum, r) => sum + r.high, 0)),
});
const planningRow = (room, count, low, high) => ({
  room, count, per_room_square_feet: { low, high }, total_square_feet: scaled({ low, high }, count),
});

function planSpace(a) {
  const dental = a.practice_type === 'dental';
  if (dental ? a.operatories === 0 || a.exam_rooms !== 0 : a.exam_rooms === 0 || a.operatories !== 0) {
    throw new Error('incompatible room program');
  }
  const veterinary = a.practice_type === 'veterinary';
  const rooms = [
    dental ? planningRow('Operatories', a.operatories, 110, 140)
      : planningRow('Exam rooms', a.exam_rooms, veterinary ? 120 : 100, veterinary ? 160 : 120),
    planningRow('Provider workrooms', a.providers, 80, 100),
    planningRow('Reception', 1, 160, 220), planningRow('Waiting', a.providers, 100, 150),
    dental ? planningRow('Sterilization and lab', 1, 160, 240)
      : planningRow('Clinical support', 1, veterinary ? 240 : 120, veterinary ? 360 : 180),
    planningRow('Staff support', a.providers, 60, 90), planningRow('Storage', 1, 100, 160),
    planningRow('Toilets', Math.ceil(a.providers / 3), 100, 140), planningRow('Mechanical support', 1, 60, 100),
  ];
  if (veterinary) rooms.push(planningRow('Animal holding', 1, 160, 240));
  const subtotal = summed(rooms.map(r => r.total_square_feet));
  rooms.push(planningRow('Circulation', 1, Math.ceil(subtotal.low * .25), Math.ceil(subtotal.high * .35)));
  const usable = summed(rooms.map(r => r.total_square_feet));
  return {
    practice_type: a.practice_type, usable_square_feet: usable,
    rentable_square_feet: { low: Math.ceil(usable.low * 1.1), high: Math.ceil(usable.high * 1.2) },
    rooms,
    assumptions: [
      'Original V0 planning allowances dated 2026-10-01; square feet are approximate.',
      'Usable area includes circulation of 25% to 35% of room area.',
      'Rentable area adds an assumed 10% to 20% common-area load to usable area.',
    ],
    limitations: [
      'Outpatient planning only; specialized imaging, surgery and inpatient uses need a separate program.',
      'An architect must verify equipment clearances, accessibility, code, utilities and clinical workflow before selecting space.',
    ], notice: NOTICE,
  };
}

function estimateOccupancy(a) {
  const components = rates.lease_types[a.lease_type].map(c => ({
    ...c, annual_cost: scaled(c.annual_rate, a.square_feet),
  }));
  const annual = summed(components.map(c => c.annual_cost));
  // Explicit projection: source URLs and authorization metadata never enter answers.
  const { title, data_date, retrieved_date, observed_annual_rate, method } = rates.source;
  return {
    ...a, components, annual_cost: annual, monthly_cost: scaled(annual, 1 / 12),
    source: { title, data_date, retrieved_date, observed_annual_rate, method },
    assumptions: [
      'Every range is an estimate in USD per rentable square foot per year, or USD for the stated period.',
      'The report is mixed general-office asking rent; 81.22% of reported asking rates are full service.',
      'Lease-type offsets and utilities are scenario assumptions, not observed healthcare expense data.',
    ], limitations: [
      'Source data are dated 2024-12-31; this is not a current quote or a medical-office market survey.',
      'Only Downtown Mobile is supported; other Gulf Coast markets need authorized dated sources.',
      'Excludes build-out, TI financing, equipment, deposits, moving costs, parking and practice operations; confirm lease inclusions.',
    ], notice: NOTICE,
  };
}

function compareBuy(a) {
  const down = a.purchase_price * a.down_payment_percent / 100;
  const principal = a.purchase_price - down;
  const n = a.loan_term_years * 12;
  const months = Math.min(a.holding_years * 12, n);
  const rate = a.interest_rate_percent / 1200;
  // log1p/expm1 avoid cancellation for very small positive interest rates.
  const payment = principal === 0 ? 0 : rate === 0 ? principal / n
    : principal * rate / -Math.expm1(-n * Math.log1p(rate));
  const growth = Math.expm1(months * Math.log1p(rate));
  const balance = months === n || principal === 0 ? 0 : Math.max(0,
    rate === 0 ? principal - payment * months : principal * (1 + growth) - payment * growth / rate);
  const upfront = down + a.purchase_price * a.closing_cost_percent / 100;
  const buyCash = upfront + payment * months + a.annual_owner_cost * a.holding_years;
  const proceeds = a.purchase_price * (1 - a.sale_cost_percent / 100) - balance;
  return {
    assumptions: a, monthly_loan_payment: round(payment), upfront_buy_cash: round(upfront),
    lease_total_cash: round(a.square_feet * a.annual_lease_rate * a.holding_years),
    buy_total_cash_before_sale: round(buyCash), remaining_loan_balance: round(balance),
    net_sale_proceeds: round(proceeds), buy_net_cost_after_sale: round(buyCash - proceeds),
    limitations: [
      'Scenario comparison only; neither option is recommended automatically.',
      'Assumes level lease rent, fixed-rate fully amortizing debt, no appreciation and sale at the input purchase price.',
      'Ignores tax effects, opportunity cost, inflation, major repairs, build-out and financing eligibility; compare equivalent lease/owner cost inclusions.',
    ], notice: NOTICE,
  };
}

function leaseChecklist(a) {
  const items = [
    { topic: 'Tenant improvements', questions: [
      'What TI allowance is available, when is it paid, and who covers overruns?',
      'Who approves plans, owns improvements and handles restoration at lease end?',
    ] },
    { topic: 'Exclusivity', questions: ['Can the landlord agree to a defined healthcare-use exclusivity, and what are its exceptions and remedies?'] },
    { topic: 'Permitted use', questions: ['Does the use clause cover the intended services and future practice changes, subject to zoning and licensing?'] },
    { topic: 'Assignment and practice sale', questions: [
      'Can the lease transfer with a practice sale or ownership change, and when is consent required?',
      'Is the original tenant or guarantor released after an approved assignment?',
    ] },
    { topic: 'HVAC and plumbing', questions: [
      'Can HVAC capacity, after-hours service, plumbing, electrical loads and equipment connections support the proposed medical use?',
      'Who pays for upgrades, maintenance, repairs and replacement?',
    ] },
    { topic: 'Timing and recurring costs', questions: [
      'When does rent start relative to delivery, permits and build-out?',
      'Which operating costs, tax increases, utilities and capital expenses can pass through, with what caps and audit rights?',
    ] },
    { topic: 'Access and exit', questions: ['What parking, accessibility, signage, renewal, casualty and early-exit terms are negotiable?'] },
  ];
  if (a.practice_type === 'dental') items.push({ topic: 'Dental equipment', questions: [
    'Can suction, compressed air, sterilization and chair plumbing be installed with required permits?',
  ] });
  if (a.practice_type === 'veterinary') items.push({ topic: 'Veterinary operations', questions: [
    'Are animal holding, waste handling, noise control and ventilation compatible with the permitted use?',
  ] });
  return { practice_type: a.practice_type, items, limitations: [
    'Every term is negotiable; the checklist does not determine enforceability or replace a qualified attorney.',
    'Verify zoning, permits, licensing and physical suitability with the relevant professionals.',
  ], notice: NOTICE };
}

function definition(name, description, input, output, calculate) {
  const json = schema => {
    const { $schema, ...body } = z.toJSONSchema(schema, { target: 'draft-7' });
    return body;
  };
  return { name, description, inputSchema: json(input), outputSchema: json(output),
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    input, output, calculate };
}

export const TOOLS = [
  definition('plan_practice_space',
    'Estimate educational room-by-room usable and rentable space for an outpatient dental, medical or general small-animal veterinary practice. Original planning assumptions; not a code or clinical standard.',
    spaceInput, z.strictObject({
      practice_type: practice, usable_square_feet: range, rentable_square_feet: range,
      rooms: z.array(z.strictObject({ room: z.string(), count: z.number().int().positive(), per_room_square_feet: range, total_square_feet: range })),
      assumptions: textList, ...common,
    }), planSpace),
  definition('estimate_occupancy_cost',
    'Estimate educational annual and monthly occupancy ranges from a bundled dated Downtown Mobile general-office survey anchor and disclosed expense assumptions. Only Downtown Mobile is supported; not a current healthcare rent quote.',
    occupancyInput, z.strictObject({
      ...occupancyInput.shape,
      components: z.array(z.strictObject({ component: z.string(), annual_rate: range, annual_cost: range, basis: z.string() })),
      annual_cost: range, monthly_cost: range,
      source: z.strictObject({ title: z.string(), data_date: z.string(), retrieved_date: z.string(), observed_annual_rate: money, method: z.string() }),
      assumptions: textList, ...common,
    }), estimateOccupancy),
  definition('compare_lease_buy',
    'Calculate an educational lease-versus-buy cash scenario from numeric assumptions, showing debt, owner costs and sale proceeds. No tax, appreciation or financing recommendation.',
    buyInput, z.strictObject({
      assumptions: buyInput, monthly_loan_payment: money, upfront_buy_cash: money,
      lease_total_cash: money, buy_total_cash_before_sale: money, remaining_loan_balance: money,
      net_sale_proceeds: z.number().finite(), buy_net_cost_after_sale: z.number().finite(), ...common,
    }), compareBuy),
  definition('get_healthcare_lease_checklist',
    'Provide an educational checklist of negotiable LOI and lease questions for a healthcare tenant, including TI, exclusivity, use, assignment, HVAC and plumbing. Not legal advice or an enforceability assessment.',
    z.strictObject({ practice_type: practice }), z.strictObject({ practice_type: practice,
      items: z.array(z.strictObject({ topic: z.string(), questions: textList })), ...common,
    }), leaseChecklist),
  definition('get_broker_search_help',
    'Return a plain CARR description and an informational page only after an explicit user request for help with an actual property search. Do not use for general educational planning or unsolicited referrals. No contact capture or search is performed.',
    z.strictObject({ request: z.literal('actual_search_help') }), z.strictObject({
      description: z.string(), informational_page: z.literal(INFORMATIONAL_PAGE), ...common,
    }), () => ({
      description: 'CARR is a commercial real estate brokerage that represents healthcare tenants and buyers.',
      informational_page: INFORMATIONAL_PAGE, limitations: [
        'Placeholder informational page; a published page must be configured before submission.',
        'No property search, contact capture or message is performed by this tool.',
      ], notice: NOTICE,
    })),
];

// Cross-field schema constraints match the runtime room-program check.
TOOLS[0].inputSchema.allOf = [{
  if: { type: 'object', properties: { practice_type: { const: 'dental' } }, required: ['practice_type'] },
  then: { type: 'object', properties: { operatories: { type: 'integer', minimum: 1 }, exam_rooms: { const: 0 } } },
  else: { type: 'object', properties: { exam_rooms: { type: 'integer', minimum: 1 }, operatories: { const: 0 } } },
}];

export function callTool(name, args) {
  const tool = TOOLS.find(t => t.name === name);
  if (!tool) return refusal('Unknown planning tool.');
  const parsed = tool.input.safeParse(args);
  if (!parsed.success) {
    if (name === 'get_broker_search_help') return refusal('Use this informational tool only after an explicit request for help with an actual property search.');
    if (name === 'estimate_occupancy_cost') return refusal('Unsupported planning input. Only the dated Downtown Mobile table is available; do not supply contact or listing data.');
    return refusal('Invalid planning input. Use only the declared fields and supported numeric ranges; do not supply personal or listing data.');
  }
  try {
    const result = tool.output.parse(tool.calculate(parsed.data));
    return { content: [{ type: 'text', text: JSON.stringify(result) }], structuredContent: result };
  } catch {
    return refusal('Invalid planning input. Check compatible room counts and numeric assumptions; professional review is required.');
  }
}

function refusal(error) {
  // Never reflect raw input, schema errors, URLs, identity or diagnostics.
  return { isError: true, content: [{ type: 'text', text: JSON.stringify({ error }) }], structuredContent: { error } };
}
