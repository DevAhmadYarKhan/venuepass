/** Safe reusable management controls and guards for identity/selection changes. */
import { authVersion, currentUser } from "./auth.js";
import { element } from "./ui.js";

/** A controller generation and authentication generation jointly own every result. */
export function sessionGuard(valid) {
  const version = authVersion();
  const identity = currentUser()?.id;
  return () => valid() && identity === currentUser()?.id && version === authVersion();
}

/** Build labeled fields without interpolating user-controlled HTML. */
export function field(label, name, type = "text", required = true, value = "") {
  const wrapper = element("label", label);
  const input = element(type === "textarea" ? "textarea" : "input");
  if (type !== "textarea") input.type = type;
  input.name = name; input.required = required; input.value = value;
  wrapper.append(input);
  return wrapper;
}

/** Actions are never implicit form submits and can be disabled during mutations. */
export function button(label, action) {
  const control = element("button", label, "secondary");
  control.type = "button"; control.addEventListener("click", action);
  return control;
}

/** Announce errors and pending state to keyboard and screen-reader users. */
export function message() {
  const node = element("p"); node.setAttribute("role", "status");
  node.setAttribute("aria-live", "polite"); return node;
}

/** Only retained current-view results may change a status message. */
export function report(node, text, error = false) {
  node.textContent = text; node.classList.toggle("error", error);
}

/** Unknown mutation outcomes require inspection, never an automatic creation retry. */
export function failure(error, creation = false) {
  return creation && (!error.status || error.status >= 500)
    ? `${error.message} The outcome is uncertain. Refresh the list and inspect it before submitting again.`
    : error.message;
}

/** A small accessible pager uses the same bounded limit/offset API convention. */
export function pager(offset, count, size, change) {
  const nav = element("nav", undefined, "pagination");
  nav.setAttribute("aria-label", "Management pages");
  const previous = button("Previous page", () => change(offset - size));
  const next = button("Next page", () => change(offset + size));
  previous.disabled = offset === 0; next.disabled = count < size;
  nav.append(previous, element("span", `Page ${offset / size + 1}`), next);
  return nav;
}
