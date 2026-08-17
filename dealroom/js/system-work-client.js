import { uuidv4 } from "./uuid.js";
import { validateHumanRef } from "./system-work-view.js";

export function createSystemWorkClient(options = {}) {
  const fetchImpl = options.fetchImpl || ((path, init) => fetch(path, init));
  const uuid = options.uuid || uuidv4;
  let session = null;

  async function decode(response) {
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(body.message || body.hint || body.error || `Request failed (${response.status})`);
      error.status = response.status;
      error.code = body.error || "request_failed";
      error.payload = body;
      throw error;
    }
    return body;
  }

  async function bootstrap() {
    session = await decode(await fetchImpl("/api/system-work/session", {
      credentials: "same-origin", headers: { accept: "application/json" },
    }));
    return session;
  }

  function headers(challenge) {
    if (!session?.csrf_token) throw new Error("System work session is not ready.");
    return { "content-type": "application/json", accept: "application/json",
      "x-carr-csrf": session.csrf_token,
      ...(challenge ? { "x-carr-action-challenge": challenge } : {}) };
  }

  async function post(path, body, challenge) {
    const response = await fetchImpl(path, { method: "POST", credentials: "same-origin",
      headers: headers(challenge), body: JSON.stringify(body) });
    const envelope = await decode(response);
    return envelope.data ?? envelope;
  }

  async function challenge(action, material) {
    return post("/api/system-work/challenge", { action, ...material });
  }

  const withKey = (body) => ({ ...body, idempotency_key: body.idempotency_key || uuid() });

  return {
    get session() { return session; },
    bootstrap,
    async read(humanRef) {
      const ref = validateHumanRef(humanRef);
      const response = await fetchImpl(`/api/system-work/${encodeURIComponent(ref)}`, {
        credentials: "same-origin", headers: { accept: "application/json" },
      });
      const envelope = await decode(response);
      return envelope.data ?? envelope;
    },
    report: (body) => post("/api/system-work/report", withKey(body)),
    triage: (humanRef, body) => post(`/api/system-work/${validateHumanRef(humanRef)}/triage`, withKey(body)),
    preparePlan: (humanRef, body) => post(`/api/system-work/${validateHumanRef(humanRef)}/plan`, withKey(body)),
    async acceptPlan(humanRef, body) {
      const ref = validateHumanRef(humanRef);
      const requestBody = withKey(body);
      const material = { action: "accept-ready-plan", human_ref: ref,
        base_version: requestBody.base_version, plan_hash: requestBody.plan_hash,
        idempotency_key: requestBody.idempotency_key };
      const receipt = await challenge(material.action, { human_ref: ref,
        base_version: requestBody.base_version, plan_hash: requestBody.plan_hash,
        idempotency_key: requestBody.idempotency_key });
      return post(`/api/system-work/${ref}/plan/accept`, requestBody, receipt.challenge);
    },
    proposeOutcome: (humanRef, body) => post(`/api/system-work/${validateHumanRef(humanRef)}/outcomes`, withKey(body)),
    async acceptOutcome(humanRef, body) {
      const ref = validateHumanRef(humanRef);
      const requestBody = withKey(body);
      const receipt = await challenge("accept-outcome-feedback", { human_ref: ref,
        base_version: requestBody.base_version, feedback_hash: requestBody.feedback_hash,
        idempotency_key: requestBody.idempotency_key });
      return post(`/api/system-work/${ref}/outcomes/accept`, requestBody, receipt.challenge);
    },
  };
}
