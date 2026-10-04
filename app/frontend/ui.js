/** Shared safe DOM primitives, reused by discovery, authentication, and booking views. */
export const $ = id => document.getElementById(id);

/** Render user-controlled API values as text, never interpreted markup. */
export function element(tag, text, className = "") {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

/** Include the viewer's timezone so dates do not silently change their meaning. */
export function dateLabel(value) {
  return new Date(value).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })
    + ` (${Intl.DateTimeFormat().resolvedOptions().timeZone})`;
}

/** Update a live region without inserting server-provided markup. */
export function status(id, text, error = false) {
  $(id).textContent = text;
  $(id).classList.toggle("error", error);
}
