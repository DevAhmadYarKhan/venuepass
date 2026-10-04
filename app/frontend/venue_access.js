/** Venue owners authorize organizers by UUID without exposing an account directory. */
import { authRequest } from "./auth.js";
import { element } from "./ui.js";
import { button, field, message, report, failure, pager } from "./management.js";

window.addEventListener("venueselected", event => {
  const { venue, root, current } = event.detail;
  let generation = 0;
  let offset = 0;
  let busy = false;
  const pageSize = 10;

  /** Refresh explicit grants; a newer page supersedes an older network response. */
  async function load() {
    const version = ++generation;
    const latest = () => current() && version === generation;
    const note = message(); root.replaceChildren(element("h3", "Organizer access"), note);
    report(note, "Loading authorized organizers…");
    try {
      const rows = await authRequest(`/venues/${venue.id}/organizers?limit=${pageSize}&offset=${offset}`);
      if (!latest()) return;
      report(note, rows.length ? "Explicitly authorized organizer IDs:" : "No explicit organizer grants on this page.");
      root.append(element("p", "The owner can host with organizer permission without an explicit grant. Revocation affects future event creation, not existing events."));
      const list = element("ul");
      for (const identity of rows) {
        const row = element("li", identity + " ");
        row.append(button("Revoke access", () => {
          if (!busy && latest() && window.confirm(`Revoke future hosting access for organizer ${identity}? Existing events are unchanged.`)) change(identity, false, note, latest);
        }));
        list.append(row);
      }
      const form = element("form", undefined, "management-form");
      const input = field("Organizer account UUID", "organizer_id");
      input.lastChild.pattern = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";
      const submit = element("button", "Grant access"); submit.type = "submit";
      form.append(input, submit);
      form.addEventListener("submit", event => {
        event.preventDefault();
        if (!busy && latest()) change(new FormData(form).get("organizer_id"), true, note, latest);
      });
      root.append(list, pager(offset, rows.length, pageSize, value => { offset = value; load(); }),
        button("Refresh organizer access", load), form);
    } catch (error) { if (latest()) { report(note, error.message, true); root.append(button("Retry organizer access", load)); } }
  }

  /** Both grant and revocation are repeatable; freeze controls until one completes. */
  async function change(identity, grant, note, latest) {
    busy = true;
    const controls = [...root.querySelectorAll("input, button")].map(node => [node, node.disabled]);
    controls.forEach(([node]) => { node.disabled = true; });
    report(note, grant ? "Granting access…" : "Revoking access…");
    try {
      await authRequest(`/venues/${venue.id}/organizers/${identity}`, { method: grant ? "PUT" : "DELETE" });
      if (latest()) { offset = 0; await load(); }
    } catch (error) { if (latest()) report(note, failure(error), true); }
    finally {
      busy = false;
      // Restore the pager's original state on failure; a successful refresh owns new controls.
      if (latest()) controls.forEach(([node, disabled]) => {
        if (root.contains(node)) node.disabled = disabled;
      });
    }
  }
  load();
});
