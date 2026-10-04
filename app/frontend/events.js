/** Organizer-owned event discovery and creation through the independent JSON API. */
import { authRequest, currentUser } from "./auth.js";
import { $, element, dateLabel } from "./ui.js";
import { button, field, message, report, failure, sessionGuard, pager } from "./management.js";

let generation = 0;
let offset = 0;
let selection = 0;
const pageSize = 6;

/** Render owned history, keeping cancelled and started events visible. */
async function loadEvents() {
  if (!currentUser()?.is_organizer) return;
  const version = ++generation; ++selection;
  const current = sessionGuard(() => version === generation);
  const root = $("organizer-content"); root.replaceChildren();
  const note = message(); root.append(note); report(note, "Loading your events…");
  try {
    const rows = await authRequest(`/users/me/events?limit=${pageSize}&offset=${offset}`);
    if (!current()) return;
    report(note, rows.length ? "Your events, including past and cancelled events." : "No events on this page.");
    const list = element("div", undefined, "event-grid");
    const detail = element("section", undefined, "detail"); detail.hidden = true;
    for (const event of rows) {
      const state = event.cancelled_at ? "Cancelled" : new Date(event.starts_at) <= new Date() ? "Started" : "Upcoming";
      const card = element("article", undefined, "event-card");
      card.append(element("h2", event.name), element("span", state, "badge"), element("p", dateLabel(event.starts_at)),
        button("Manage event", () => {
          const selected = ++selection;
          detail.hidden = false;
          detail.replaceChildren(element("h2", event.name), element("p", `Event ID: ${event.id}`),
            element("p", event.description || "No description provided."), element("p", state));
          window.dispatchEvent(new CustomEvent("eventselected", { detail: {
            event, root: detail, current: () => current() && selected === selection, refresh: loadEvents,
          } }));
        }));
      list.append(card);
    }
    root.append(button("Refresh my events", loadEvents), list,
      pager(offset, rows.length, pageSize, value => { offset = value; loadEvents(); }), creationForm(current), detail);
  } catch (error) { if (current()) { report(note, error.message, true); root.append(button("Retry my events", loadEvents)); } }
}

/** Use authorized venue choices; dates are interpreted in the user's displayed timezone. */
function creationForm(current) {
  const form = element("form", undefined, "management-form");
  const name = field("Event name", "name"); name.lastChild.maxLength = 255;
  const description = field("Description", "description", "textarea", false);
  const venueLabel = element("label", "Authorized venue");
  const venues = element("select"); venues.name = "venue_id"; venues.required = true;
  venueLabel.append(venues);
  const starts = field("Starts at", "starts_at", "datetime-local");
  const ends = field("Ends at (optional)", "ends_at", "datetime-local", false);
  const submit = element("button", "Create event"); submit.type = "submit"; submit.disabled = true;
  const note = message();
  let choicesVersion = 0;
  form.append(element("h2", "Create an event"), name, description, venueLabel, starts, ends,
    element("p", `Dates use your timezone: ${Intl.DateTimeFormat().resolvedOptions().timeZone}. Capacity is derived from venue seats.`), submit, note);

  /** Fetch bounded authorized pages rather than filtering public venue records. */
  async function loadChoices() {
    const version = ++choicesVersion;
    const latest = () => current() && version === choicesVersion;
    venues.replaceChildren(); submit.disabled = true; report(note, "Loading authorized venues…");
    try {
      for (let venueOffset = 0; latest(); venueOffset += 100) {
        const rows = await authRequest(`/users/me/hosting-venues?limit=100&offset=${venueOffset}`);
        if (!latest()) return;
        for (const venue of rows) {
          const option = element("option", `${venue.name} — ${venue.address}`); option.value = venue.id; venues.append(option);
        }
        if (rows.length < 100) break;
      }
      if (!latest()) return;
      submit.disabled = !venues.options.length;
      report(note, venues.options.length ? "Only venues you currently own or are authorized to use are listed."
        : "No authorized venues. Ask a venue owner to grant access using your account UUID, or create a venue if you have venue-manager permission.");
    } catch (error) { if (latest()) report(note, error.message, true); }
  }
  form.append(button("Refresh authorized venues", loadChoices)); loadChoices();
  form.addEventListener("submit", async event => {
    event.preventDefault(); if (submit.disabled || !current()) return;
    const data = new FormData(form);
    const start = new Date(data.get("starts_at"));
    const end = data.get("ends_at") ? new Date(data.get("ends_at")) : null;
    if (isNaN(start) || start <= new Date() || (end && (isNaN(end) || end <= start))) {
      report(note, "Choose a future start and, if supplied, an end after the start.", true); return;
    }
    const payload = { name: data.get("name"), description: data.get("description") || null,
      venue_id: data.get("venue_id"), starts_at: start.toISOString(), ends_at: end?.toISOString() || null };
    const controls = [...form.querySelectorAll("input, textarea, select, button")];
    controls.forEach(node => { node.disabled = true; }); report(note, "Creating event…");
    try {
      await authRequest("/events", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      if (current()) { offset = 0; loadEvents(); window.dispatchEvent(new Event("eventschanged")); }
    } catch (error) { if (current()) report(note, failure(error, true), true); }
    finally { if (current()) controls.forEach(node => { node.disabled = false; }); }
  });
  return form;
}

window.addEventListener("routechange", event => { if (event.detail.route === "/organizer") loadEvents(); });
window.addEventListener("authchange", () => { ++generation; ++selection; offset = 0; });
