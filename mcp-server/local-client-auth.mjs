// Pure credential selection for local-verb.mjs.  The caller selects a bounded
// client purpose; the Worker still derives identity from the bearer token.
// Token values never appear in the returned notice or in process arguments.

const CLIENT_PROFILES = Object.freeze({
  local: Object.freeze({
    tokenVariable: "CARR_MCP_LOCAL_TOKEN",
    identityNotice: "local machine actor (via local-token)",
  }),
  "hermes-projector": Object.freeze({
    tokenVariable: "CARR_HERMES_MCP_TOKEN",
    identityNotice: "Hermes queue projector (via hermes-token)",
  }),
});

function tokenFromFile(raw, variable) {
  for (const line of String(raw || "").split("\n")) {
    const text = line.trim();
    if (text.startsWith(`${variable}=`))
      return text.slice(variable.length + 1).trim().replace(/^['"]|['"]$/g, "");
  }
  return "";
}

export function selectLocalClientCredential(env = {}, tokenFileRaw = "") {
  const profile = env.CARR_MCP_CLIENT_PROFILE || "local";
  const config = CLIENT_PROFILES[profile];
  if (!config)
    throw new Error(`unsupported local MCP client profile: ${profile}`);
  const token = env[config.tokenVariable] || tokenFromFile(tokenFileRaw, config.tokenVariable);
  return { profile, token, ...config };
}
