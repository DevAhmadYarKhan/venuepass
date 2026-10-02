/** Public event discovery and label-based seat browsing using the real API. */
import { request, allPages } from "./api.js";

import { $, element, dateLabel, status } from "./ui.js";
import "./auth.js";

const pageSize = 6;
let offset = 0;
let browseVersion = 0;
let detailVersion = 0;
let lastDetailButton;
let venues = new Map();
let appliedFilters = new URLSearchParams({ upcoming_only: "true" });

/** Convert local form dates to absolute instants; URLSearchParams handles timezone escaping. */
function filterQuery() {
  const data = new FormData($("filters"));
  const params = new URLSearchParams();
  for (const name of ["q", "venue_id", "starts_from", "starts_before"]) {
    const value = data.get(name).trim();
    if (value) params.set(name, name.startsWith("starts_") ? new Date(value).toISOString() : value);
  }
  params.set("upcoming_only", String(data.has("upcoming_only")));
  return params;
}

/** Ignore older results when a newer filter submission overtakes a slow request. */
async function browse() {
  const version = ++browseVersion;
  $("previous").disabled = $("next").disabled = true;
  $("event-list").replaceChildren();
  status("browse-status", "Loading events…");
  try {
    const params = new URLSearchParams(appliedFilters);
    params.set("limit", String(pageSize));
    params.set("offset", String(offset));
    const rows = await request(`/events?${params}`);
    if (version !== browseVersion) return;
    for (const event of rows) {
      const card = element("article", undefined, "event-card");
      card.append(element("span", new Date(event.starts_at) > new Date() ? "Upcoming" : "Started", "badge"),
        element("h3", event.name), element("p", dateLabel(event.starts_at)),
        element("p", dateLabelVenue(event.venue_id), "venue-name"));
      card.querySelector(".venue-name").dataset.venueId = event.venue_id;
      const button = element("button", "View event & seats");
      button.type = "button";
      button.addEventListener("click", () => showEvent(event.id, button));
      card.append(button);
      $("event-list").append(card);
    }
    status("browse-status", rows.length ? `${rows.length} events on this page.` : "No events found. Try different filters, or create an event through the API docs.");
    $("page-label").textContent = `Page ${offset / pageSize + 1}`;
    $("previous").disabled = offset === 0;
    $("next").disabled = rows.length < pageSize;
  } catch (error) {
    if (version !== browseVersion) return;
    status("browse-status", error.message, true);
    $("previous").disabled = offset === 0;
  }
}

/** Fetch event and every seat page; late responses cannot replace a newer selection. */
async function showEvent(id, button) {
  const version = ++detailVersion;
  lastDetailButton = button;
  $("event-detail").hidden = false;
  $("detail-heading").textContent = "Event details";
  $("detail-heading").focus();
  $("detail-copy").replaceChildren();
  $("seat-list").replaceChildren();
  status("seat-status", "Loading event and seats…");
  try {
    const [event, seats] = await Promise.all([request(`/events/${id}`), allPages(`/events/${id}/seats`, 500)]);
    if (version !== detailVersion) return;
    $("detail-heading").textContent = event.name;
    $("detail-copy").append(element("p", dateLabel(event.starts_at)),
      element("p", event.description || "No description provided."));
    renderSeats(seats);
    status("seat-status", `${seats.filter(seat => seat.is_available).length} of ${seats.length} seats available. Availability can change.`);
  } catch (error) { if (version === detailVersion) status("seat-status", error.message, true); }
}

/** Group labels without claiming their spacing corresponds to a physical floor plan. */
function renderSeats(seats) {
  const groups = new Map();
  for (const seat of seats) {
    const key = JSON.stringify([seat.section, seat.row]);
    if (!groups.has(key)) groups.set(key, { label: `${seat.section} · Row ${seat.row}`, seats: [] });
    groups.get(key).seats.push(seat);
  }
  for (const group of groups.values()) {
    const block = element("div", undefined, "seat-group");
    const labels = element("div", undefined, "seats");
    for (const seat of group.seats) labels.append(element("span", `${seat.number} · ${seat.is_available ? "available" : "unavailable"}`, `seat${seat.is_available ? "" : " unavailable"}`));
    block.append(element("h4", group.label), labels);
    $("seat-list").append(block);
  }
}

// Page navigation reuses submitted filters, never unfinished edits in the form.
$("filters").addEventListener("submit", event => { event.preventDefault(); appliedFilters = filterQuery(); offset = 0; browse(); });
$("previous").addEventListener("click", () => { offset -= pageSize; browse(); });
$("next").addEventListener("click", () => { offset += pageSize; browse(); });
$("close-detail").addEventListener("click", () => { ++detailVersion; $("event-detail").hidden = true; lastDetailButton?.focus(); });

/** Venue names enhance browsing, but an unavailable venue list must not block events. */
function dateLabelVenue(id) { return venues.get(id)?.name || "Venue"; }

async function loadVenues() {
  try {
    const rows = await allPages("/venues", 100);
    venues = new Map(rows.map(venue => [venue.id, venue]));
    for (const venue of rows) {
      const option = element("option", venue.name);
      option.value = venue.id;
      $("venue-filter").append(option);
    }
    for (const label of document.querySelectorAll(".venue-name")) label.textContent = dateLabelVenue(label.dataset.venueId);
  } catch { /* Event browsing remains usable without the optional venue-name lookup. */ }
}
// Optional venue enrichment never blocks the primary event list.
browse();
loadVenues();
