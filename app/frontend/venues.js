/** Owner-only venue preparation using existing JSON venue and physical-seat APIs. */
import { authRequest, currentUser } from "./auth.js";
import { $, element } from "./ui.js";
import { button, field, message, report, failure, sessionGuard, pager } from "./management.js";

let generation = 0;
let offset = 0;
let selection = 0;
const pageSize = 6;

/** Render owned venues and a creation form; stale sessions cannot publish results. */
async function loadVenues() {
  if (!currentUser()?.is_venue_manager) return;
  const version = ++generation; ++selection;
  const current = sessionGuard(() => version === generation);
  const root = $("venues-content"); root.replaceChildren();
  const note = message(); report(note, "Loading your venues…"); root.append(note);
  try {
    const rows = await authRequest(`/users/me/venues?limit=${pageSize}&offset=${offset}`);
    if (!current()) return;
    report(note, rows.length ? "Select a venue to manage its seats and organizer access." : "No venues on this page. Create one below.");
    const list = element("div", undefined, "event-grid");
    const detail = element("section", undefined, "detail"); detail.hidden = true;
    for (const venue of rows) {
      const card = element("article", undefined, "event-card");
      card.append(element("h2", venue.name), element("p", venue.address),
        button("Manage venue", () => showVenue(venue, detail, current)));
      list.append(card);
    }
    root.append(button("Refresh venues", loadVenues), list,
      pager(offset, rows.length, pageSize, value => { offset = value; loadVenues(); }),
      venueForm(current), detail);
  } catch (error) { if (current()) { report(note, error.message, true); root.append(button("Retry venue list", loadVenues)); } }
}

/** Creation has no retry identity, so uncertain responses require manual inspection. */
function venueForm(current) {
  const form = element("form", undefined, "management-form");
  const name = field("Venue name", "name"); name.lastChild.maxLength = 255;
  const address = field("Address", "address"); address.lastChild.maxLength = 1000;
  const submit = element("button", "Create venue"); submit.type = "submit";
  const note = message();
  form.append(element("h2", "Create a venue"), name, address, submit, note);
  form.addEventListener("submit", async event => {
    event.preventDefault(); if (submit.disabled || !current()) return;
    submit.disabled = true; report(note, "Creating venue…");
    const data = new FormData(form);
    try {
      await authRequest("/venues", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: data.get("name"), address: data.get("address") }) });
      if (current()) { offset = 0; loadVenues(); window.dispatchEvent(new Event("venueschanged")); }
    } catch (error) { if (current()) report(note, failure(error, true), true); }
    finally { if (current()) submit.disabled = false; }
  });
  return form;
}

/** Selection owns physical-seat responses independently of the outer venue page. */
function showVenue(venue, root, pageCurrent) {
  const version = ++selection;
  const current = () => pageCurrent() && version === selection;
  root.hidden = false; root.replaceChildren(element("h2", venue.name), element("p", venue.address),
    element("p", `Venue ID: ${venue.id}`),
    element("p", "Added seats are offered by future events. Existing events keep their original seats."));
  const seats = element("section");
  const access = element("section"); access.id = "venue-access";
  root.append(seats, seatForm(venue, current, () => listSeats(venue, seats, 0, current)), access);
  listSeats(venue, seats, 0, current);
  // The access module subscribes separately so its commit adds only that workflow.
  window.dispatchEvent(new CustomEvent("venueselected", { detail: { venue, root: access, current } }));
}

/** Browse every seat page without fetching an unbounded layout into one view. */
async function listSeats(venue, root, seatOffset, current) {
  // A newer page or refresh owns the results even within the same selected venue.
  const version = Number(root.dataset.version || 0) + 1;
  root.dataset.version = String(version);
  const latest = () => current() && root.dataset.version === String(version);
  const note = message(); root.replaceChildren(element("h3", "Physical seats"), note);
  report(note, "Loading seats…");
  try {
    const rows = await authRequest(`/venues/${venue.id}/seats?limit=20&offset=${seatOffset}`);
    if (!latest()) return;
    report(note, rows.length ? `${rows.length} seats on this page.` : "No seats on this page.");
    const list = element("ul");
    for (const seat of rows) list.append(element("li", `${seat.section} · Row ${seat.row} · Seat ${seat.number}`));
    root.append(list, pager(seatOffset, rows.length, 20, value => listSeats(venue, root, value, current)));
  } catch (error) { if (latest()) report(note, error.message, true); }
}

/** Editable explicit seat entries support irregular layouts and atomic server batches. */
function seatForm(venue, current, refresh) {
  const form = element("form", undefined, "management-form");
  const rows = element("div", undefined, "seat-editor");
  const note = message();
  const submit = element("button", "Add seats"); submit.type = "submit";
  const add = button("Add another seat", () => appendRow());
  /** Bound explicit entries and retain backend-compatible label/number constraints. */
  function appendRow() {
    const row = element("fieldset"); row.append(element("legend", `Seat ${rows.children.length + 1}`));
    for (const [label, name, type] of [["Section", "section", "text"], ["Row", "row", "text"], ["Number", "number", "number"]]) {
      const wrapper = field(label, name, type); const input = wrapper.lastChild;
      if (type === "number") { input.min = 1; input.max = 2147483647; input.step = 1; }
      else input.maxLength = 100;
      row.append(wrapper);
    }
    row.append(button("Remove seat", () => { row.remove(); add.disabled = false; }));
    rows.append(row); add.disabled = rows.children.length >= 500;
  }
  form.append(element("h3", "Add physical seats"), rows, add, submit, note); appendRow();
  form.addEventListener("submit", async event => {
    event.preventDefault(); if (submit.disabled || !current()) return;
    if (!rows.children.length) { report(note, "Add at least one seat.", true); return; }
    const seats = [...rows.children].map(row => Object.fromEntries(
      [...row.querySelectorAll("input")].map(input => [input.name, input.type === "number" ? Number(input.value) : input.value])));
    // Capture the exact batch; edits cannot change an already-running transaction.
    const controls = [...form.querySelectorAll("input, button")]; controls.forEach(node => { node.disabled = true; });
    try {
      await authRequest(`/venues/${venue.id}/seats`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ seats }) });
      if (current()) { rows.replaceChildren(); appendRow(); report(note, "Seats added."); refresh(); }
    } catch (error) { if (current()) report(note, failure(error, true), true); }
    finally { if (current()) { form.querySelectorAll("input, button").forEach(node => { node.disabled = false; }); add.disabled = rows.children.length >= 500; } }
  });
  return form;
}

window.addEventListener("routechange", event => { if (event.detail.route === "/venues") loadVenues(); });
window.addEventListener("authchange", () => { ++generation; ++selection; offset = 0; });
