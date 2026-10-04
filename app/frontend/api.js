/** Same-origin HTTP helpers; error messages never include submitted credentials. */
export class ApiError extends Error {
  /** Preserve status and retry delay without retaining request credentials. */
  constructor(message, status = 0, retryAfter = null) { super(message); this.status = status; this.retryAfter = retryAfter; }
}

/** Decode HTTP errors separately from network failures, which may have uncertain outcomes. */
export async function request(path, options = {}) {
  // Bound waits so a stalled connection eventually offers an idempotent retry.
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  try {
    let response;
    try { response = await fetch(path, { ...options, signal: controller.signal }); }
    catch { throw new ApiError(controller.signal.aborted
      ? "The request timed out. Check your connection and try again."
      : "Cannot reach the server. Check your connection and try again."); }
    // Successful grants/revocations deliberately return no JSON body.
    if (response.status === 204) return null;
    let data;
    try { data = await response.json(); }
    catch { throw new ApiError("The server returned an unreadable response."); }
    if (!response.ok) {
      const message = Array.isArray(data.detail)
        ? data.detail.map(error => `${error.loc.slice(1).join(".") || "Request"}: ${error.msg}`).join("; ")
        : typeof data.detail === "string" ? data.detail : "The request could not be completed.";
      const retryAfter = response.headers.get("Retry-After");
      throw new ApiError(response.status === 429 && retryAfter
        ? `${message}. Try again in ${retryAfter} seconds.` : message, response.status, retryAfter);
    }
    return data;
  } finally { clearTimeout(timeout); }
}

/** Exhaust bounded API pages rather than truncating venues or larger seat layouts. */
export async function allPages(path, pageSize) {
  const rows = [];
  for (let offset = 0; ; offset += pageSize) {
    const page = await request(`${path}?limit=${pageSize}&offset=${offset}`);
    rows.push(...page);
    if (page.length < pageSize) return rows;
  }
}
