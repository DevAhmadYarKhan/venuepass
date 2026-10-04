/** Owned-event detail edits and confirmed cancellation using existing lifecycle APIs. */
import { authRequest } from "./auth.js";
import { element, dateLabel } from "./ui.js";
import { button, field, message, report, failure } from "./management.js";

window.addEventListener("eventselected", async selection => {
  const { event: selected, root, current, refresh } = selection.detail;
  const note = message(); root.replaceChildren(note); report(note, "Loading event details…");
  try {
    // The list snapshot may have become started or cancelled before selection.
    const event = await authRequest(`/events/${selected.id}`);
    if (!current()) return;
    const started = new Date(event.starts_at) <= new Date();
    const state = event.cancelled_at ? "Cancelled" : started ? "Started" : "Upcoming";
    root.replaceChildren(element("h2", event.name), element("p", `Event ID: ${event.id}`),
      element("p", dateLabel(event.starts_at)), element("p", `Capacity: ${event.capacity}`),
      element("span", state, "badge"), note);
    if (event.cancelled_at || started) {
      report(note, "Cancelled or started events cannot be edited or cancelled again from this screen.");
      root.append(element("p", event.description || "No description provided.")); return;
    }
    report(note, "Edit event details or cancel the event. Dates, venue, and seats cannot be changed.");
    const form = element("form", undefined, "management-form");
    const name = field("Event name", "name", "text", true, event.name); name.lastChild.maxLength = 255;
    const description = field("Description", "description", "textarea", false, event.description || "");
    const submit = element("button", "Save event details"); submit.type = "submit";
    form.append(name, description, submit);
    const cancel = button("Cancel event", () => {
      if (!current() || busy) return;
      if (window.confirm(`Cancel ${event.name}? All active reservations will be cancelled and their seats released. This cannot be undone.`)) mutate(true);
    });
    root.append(form, cancel);
    let busy = false;
    form.addEventListener("submit", action => { action.preventDefault(); if (current() && !busy) mutate(false); });

    /** Freeze an exact update; API locking resolves concurrent booking/cancellation. */
    async function mutate(cancelling) {
      if (!current() || busy) return;
      busy = true;
      const data = new FormData(form);
      const payload = { name: data.get("name"), description: data.get("description") || null };
      root.querySelectorAll("input, textarea, button").forEach(node => { node.disabled = true; });
      report(note, cancelling ? "Cancelling event…" : "Saving event details…");
      try {
        await authRequest(`/events/${event.id}${cancelling ? "/cancel" : ""}`, {
          method: cancelling ? "POST" : "PATCH", headers: { "Content-Type": "application/json" },
          ...(cancelling ? {} : { body: JSON.stringify(payload) }),
        });
        if (!current()) return;
        refresh();
        window.dispatchEvent(new Event("eventschanged"));
        window.dispatchEvent(new CustomEvent("refresh-event", { detail: { id: event.id } }));
        window.dispatchEvent(new Event("historychanged"));
      } catch (error) {
        if (current()) report(note, !error.status || error.status >= 500
          ? `${failure(error)} The outcome is uncertain. Refresh My events before retrying; cancellation of the same event is repeatable.`
          : error.message, true);
      } finally {
        busy = false;
        if (current()) root.querySelectorAll("input, textarea, button").forEach(node => { node.disabled = false; });
      }
    }
  } catch (error) { if (current()) report(note, error.message, true); }
});
