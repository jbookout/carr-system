// A GENERATED adversarial corpus for the client text rule (V5-J303).
//
// Hand-picked examples closed one string at a time and left the class around
// them open, round after round. This module sweeps the classes instead: every
// access-code trigger word against every ASCII punctuation mark and the common
// Unicode dashes, quotes and bullets as the connector; the same characters as
// phone separators, spaced and unspaced; and the link forms a reviewer found.
// tour-client-share-allowlist.test.mjs runs every string through the
// JavaScript rule, and the same strings are pinned in the Postgres proof
// (tour-client-share-allowlist-postgres.sql), so both engines must agree on
// each one. The lists are deterministic: regenerating gives the same strings.

// Every printable ASCII character that is not a letter or a digit.
export const ASCII_PUNCTUATION = Array.from({ length: 95 }, (_, i) => String.fromCharCode(32 + i))
  .filter(ch => !/[A-Za-z0-9]/.test(ch));
// The allowlisted Unicode dashes, quotes, bullet, ellipsis and symbols.
export const UNICODE_PUNCTUATION = ["\u2013", "\u2014", "\u2018", "\u2019", "\u201c", "\u201d", "\u2022", "\u2026", "\u00a7", "\u00b0", "\u00b1", "\u00d7"];
const CONNECTORS = [...ASCII_PUNCTUATION, ...UNICODE_PUNCTUATION];

// Each trigger word as a writer would type it, and a plural.
export const ACCESS_TRIGGERS = [
  ["Gate", "Gates"], ["Code", "Codes"], ["PIN", "PINs"], ["Combo", "Combos"], ["Combination", "Combinations"],
  ["Key", "Keys"], ["Entry", "Entries"], ["Access", "Accesses"], ["Callbox", "Callboxes"], ["Call box", "Call boxes"],
  ["Entrance", "Entrances"], ["Password", "Passwords"], ["Passcode", "Passcodes"], ["Security", "Securities"],
  ["Box", "Boxes"], ["Lockbox", "Lockboxes"], ["Lock box", "Lock boxes"], ["Keypad", "Keypads"], ["Alarm", "Alarms"],
  ["Supra", "Supras"], ["Garage", "Garages"], ["Door", "Doors"], ["Lock", "Locks"],
];

function unique(values) {
  return [...new Set(values)];
}

/** Strings that must be refused by both engines. */
export function adversarialRefused() {
  const out = [];
  for (const [word, plural] of ACCESS_TRIGGERS) {
    // Every connector character, tight and spaced ("Gate#4411", "Gate # 4411").
    for (const c of CONNECTORS) {
      out.push(`${word}${c}4411`);
      if (c !== " ") out.push(`${word} ${c} 4411`);
    }
    // Arrows and doubled connectors.
    out.push(`${word} -> 4411`, `${word}: #4411`, `${word}#: 4411`, `${word} (#4411)`, `${word} \u2014 4411`);
    // The plural, and the filler words.
    out.push(`${plural} 4411`, `${plural}: 4411`);
    for (const filler of ["is", "no.", "no", "number", "num", "num."]) out.push(`${word} ${filler} 4411`);
    // Digits split by single non-alphanumerics.
    for (const digits of ["44 11", "4-4-1-1", "4.4.1.1", "4 4 1 1", "441-1", "4/411"]) out.push(`${word}: ${digits}`);
    // A bare year, or a year followed by something that is not a building event, is a code.
    out.push(`${word} 2014 at the gate`, `${word}: 2021`, `${word} 1998`, `${word} #2020`);
  }
  // Area and zip codes: exactly three plain digits, and a real zip, are the only exemptions.
  out.push("Area code 4411", "Area code 44 11", "Area code 441-1", "Zip code 4411", "ZIP code 4411 at gate", "Zip code 3660");
  // A code-book name exempts only a bare year, never a code.
  out.push("Building code 4411", "Fire code: 4411", "Zoning code #20145");
  // Phones: every separator between 3-3-4 groups, tight, spaced and doubled.
  for (const c of CONNECTORS) {
    if (c === " ") continue;
    out.push(`251${c}555${c}0100`, `251 ${c} 555 ${c} 0100`, `251${c}${c}555${c}${c}0100`);
  }
  out.push("251 , 555 , 0100", "251 ,555 ,0100", "Call 251x555x0100", "251 x 555 x 0100");
  // Links: the reviewer's shorteners and link pages, bare and with a path, and
  // a bare name under each newer ending.
  for (const link of ["lnkd.in", "shorturl.at", "bit.do", "s.id", "amzn.to", "lnk.to", "linktr.ee", "qr.link", "t.co", "ow.ly",
    "buff.ly", "rb.gy", "cutt.ly", "is.gd", "tiny.cc", "youtu.be", "bit.ly", "goo.gl"]) {
    out.push(link, `${link}/bob`, `See ${link}/x7Q`);
  }
  for (const ending of ["xyz", "site", "online", "link", "page", "tv", "ca", "uk", "de", "estate", "land", "group", "llc", "pro", "ee", "to", "in", "at", "do", "id"]) {
    out.push(`landlord.${ending}`, `Tour at landlord.${ending} today`);
  }
  out.push("mysite.example/tour", "calendly.com/bob");
  // An ending that only starts like a unit is still a link.
  out.push("landlord.mobi/tour", "visit.miami/tour", "our.sfo/x", "go.acme/tour");
  return unique(out);
}

/** Strings that must pass both engines. */
export function adversarialAllowed() {
  const out = [];
  // (passcode and lockbox are refused as words by the lockbox rule, digits or not)
  for (const [word] of ACCESS_TRIGGERS.filter(([w]) => !/^(passcode|lock ?box)$/i.test(w))) {
    // A year followed by a building-event word is a year, for every trigger.
    out.push(`${word} 2021 upgrade`, `${word} 1998 renovation`, `${word} 2027 lease-up`, `${word} 2019 replacement`);
    // One or two digits are a door, gate or suite number, not a code.
    out.push(`${word} 2`, `${word} 12`);
  }
  out.push("Building code 2021", "Fire code 2018", "Area code 251", "Zip code 36602", "Zip code 36602-1234", "Access 24/7",
    "Security 24/7 on site");
  // Rates, ratios, dates and lists.
  out.push("$18/SF/yr", "Suite 200/210", "Floors 2/3", "3/1000 SF parking ratio", "Available 10/1/2026", "NNN $6.50/SF",
    "$24.00/sq.ft/yr NNN", "$1.25/sq.ft/mo", "Suites 201-204, 1200 SF", "1,000 SF", "120,000 SF", "Rooms 301, 302 (1200 SF)");
  // Words between the groups are not phone separators.
  out.push("Suites 250 to 300 of 1200 SF", "Floors 200 and 300, 4500 SF");
  // Every unit abbreviation before a slash is a rate, not a link.
  for (const unit of ["ft", "yr", "mo", "sf", "ac", "mi", "yd"]) out.push(`$2.50/sq.${unit}/yr`);
  return unique(out);
}
