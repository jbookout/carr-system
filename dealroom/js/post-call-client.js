const DEFAULT_LOOPBACK = 'http://127.0.0.1:4682';
const DEFAULT_HEADER = { 'X-CARR-Call-Mode': 'deal-room-v1' };

async function payload(response, fallback) {
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(body.error || fallback);
    error.status = response.status;
    error.payload = body;
    throw error;
  }
  return body;
}

/** Narrow client for the loopback-only post-call review surface. */
export function createPostCallClient(options = {}) {
  const loopback = (options.loopbackUrl || DEFAULT_LOOPBACK).replace(/\/$/u, '');
  const fetchImpl = options.fetchImpl || fetch;
  const postHeaders = { 'content-type': 'application/json', ...DEFAULT_HEADER,
    ...(options.postHeaders || {}) };

  async function loopbackFetch(path, init = {}) {
    try {
      return await fetchImpl(`${loopback}${path}`, { targetAddressSpace: 'loopback', ...init });
    } catch (cause) {
      const error = new Error('Call Mode could not reach the local post-call processor.');
      error.cause = cause;
      error.permission = true;
      throw error;
    }
  }

  return {
    async publishCallContext(context) {
      const response = await loopbackFetch('/api/call-context', {
        method: 'POST', headers: postHeaders, body: JSON.stringify(context),
      });
      return payload(response, 'Call Mode could not prepare the weekly agenda context.');
    },

    async getStatus(session) {
      const response = await loopbackFetch(`/api/post-call?session=${encodeURIComponent(session)}`, {
        headers: { ...DEFAULT_HEADER },
      });
      return payload(response, 'The post-call report could not be loaded.');
    },

    async syncStatus(session) {
      const response = await loopbackFetch('/api/post-call/sync', {
        method: 'POST', headers: postHeaders, body: JSON.stringify({ session }),
      });
      return payload(response, 'Call Mode could not verify the post-call review state.');
    },

    async createOutlookDraft(session, draftId, approvedContentHash) {
      const response = await loopbackFetch(
        `/api/post-call/drafts/${encodeURIComponent(draftId)}/create`, {
          method: 'POST', headers: postHeaders,
          body: JSON.stringify({ session, approved_content_hash: approvedContentHash }),
        });
      return payload(response, 'Outlook could not create this draft.');
    },
  };
}
