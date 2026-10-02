/** Same-origin HTTP helpers; error messages never include submitted credentials. */
export class ApiError extends Error {
  constructor(message, status = 0) { super(message); this.status = status; }
}

/** Decode HTTP errors separately from network failures, which may have uncertain outcomes. */
export async function request(path, options = {}) {
  let response;
  try { response = await fetch(path, options); }
  catch { throw new ApiError("Cannot reach the server. Check your connection and try again."); }
  let data;
  try { data = await response.json(); }
  catch { throw new ApiError("The server returned an unreadable response."); }
  if (!response.ok) {
    const message = Array.isArray(data.detail)
      ? data.detail.map(error => `${error.loc.slice(1).join(".") || "Request"}: ${error.msg}`).join("; ")
      : typeof data.detail === "string" ? data.detail : "The request could not be completed.";
    throw new ApiError(message, response.status);
  }
  return data;
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
