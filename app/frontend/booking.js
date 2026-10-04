/** Seat selection, durable-intent retries within a page, and private reservation history. */
import { authRequest, currentUser, openAuth } from "./auth.js";
import { allPages, request } from "./api.js";
import { $, dateLabel, element, status } from "./ui.js";

let activeEvent = null;
let selected = new Map();
let pending = null;
let bookingBusy = false;
let historyOffset = 0;
let historyVersion = 0;
const historyPageSize = 6;
const eventCache = new Map();
const seatCache = new Map();

/** Cache only public metadata, never private reservation responses or credentials. */
async function metadata(id) {
  if (!eventCache.has(id)) eventCache.set(id, request(`/events/${id}`));
  if (!seatCache.has(id)) seatCache.set(id, allPages(`/events/${id}/seats`, 500));
  try {
    const [event, seats] = await Promise.all([eventCache.get(id), seatCache.get(id)]);
    return { event, seats: new Map(seats.map(seat => [seat.id, seat])) };
  } catch (error) {
    // Failed lookups must be retryable rather than cached forever as rejected promises.
    eventCache.delete(id);
    seatCache.delete(id);
    throw error;
  }
}

function seatLabel(seat) { return `${seat.section} · Row ${seat.row} · Seat ${seat.number}`; }

/** Freeze selection while a request may have succeeded, preventing a second intended booking. */
function renderSelection() {
  for (const button of document.querySelectorAll("button[data-seat-id]")) {
    const chosen = selected.has(button.dataset.seatId);
    button.setAttribute("aria-pressed", String(chosen));
    button.classList.toggle("selected", chosen);
    button.disabled = button.dataset.available !== "true" || Boolean(pending);
  }
  $("selection").replaceChildren(...[...selected.values()].map(seat => element("li", seatLabel(seat))));
  $("selection-count").textContent = `${selected.size} of 20 seats selected`;
  $("reserve").disabled = !currentUser() || !activeEvent || !selected.size || Boolean(pending);
  $("booking-help").textContent = currentUser()
    ? "Review your selection below, then reserve. Selection alone does not claim seats."
    : "Log in to select and reserve seats. Selection alone does not claim seats.";
  $("pending-booking").hidden = !pending;
  if (pending) {
    $("pending-description").textContent = `A booking request for ${pending.eventName} is ${bookingBusy ? "being submitted" : "unresolved"}. Resolve it before making another booking. Keep this page open.`;
    $("retry-booking").disabled = bookingBusy;
    $("retry-booking").textContent = currentUser() ? "Retry the same booking" : "Log in to retry booking";
  }
}

/** Refresh current availability without reopening a closed detail panel or changing events. */
function refreshAvailability(id) {
  eventCache.delete(id);
  seatCache.delete(id);
  window.dispatchEvent(new CustomEvent("refresh-event", { detail: { id } }));
}

window.addEventListener("eventloading", () => {
  activeEvent = null;
  selected.clear();
  renderSelection();
});
window.addEventListener("eventloaded", event => {
  activeEvent = event.detail.event;
  selected.clear();
  renderSelection();
});
window.addEventListener("seat-toggle", event => {
  if (pending || !activeEvent) return;
  if (!currentUser()) { openAuth(); return; }
  const seat = event.detail;
  if (!seat.is_available) return;
  if (selected.has(seat.id)) selected.delete(seat.id);
  else if (selected.size < 20) selected.set(seat.id, seat);
  else { status("booking-status", "A reservation can contain at most 20 seats.", true); return; }
  status("booking-status", "");
  renderSelection();
});

/** Keep a single exact request identity across lost responses, server failures, and reauthentication. */
async function submitPending() {
  if (!pending || bookingBusy) return;
  if (!currentUser()) { openAuth(); return; }
  if (currentUser().id !== pending.userId) {
    status("booking-status", `Log out and log in as ${pending.email} to resolve this booking.`, true);
    return;
  }
  const intent = pending;
  bookingBusy = true;
  renderSelection();
  status("booking-status", "Submitting reservation…");
  try {
    const booking = await authRequest(`/events/${intent.eventId}/reservations`, {
      method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": intent.key },
      body: JSON.stringify({ seat_ids: intent.seatIds }),
    });
    pending = null;
    selected.clear();
    if (currentUser()?.id === intent.userId) {
      status("booking-status", booking.cancelled_at
        ? `The original reservation is cancelled (${booking.cancellation_reason === "event_cancelled" ? "event cancelled" : "customer cancelled"}). Its seats have not been booked again.`
        : "Reservation confirmed. Find it in My reservations below.");
      historyOffset = 0;
      loadHistory();
    } else status("booking-status", "The request completed. Log in to the original account to view the reservation.");
    refreshAvailability(intent.eventId);
  } catch (error) {
    if (!error.status || error.status >= 500 || error.status === 401) {
      // A transport/5xx failure cannot prove the transaction failed. Keep the original
      // key and seat IDs even if authentication expires before a retry reaches the API.
      intent.uncertain = true;
      status("booking-status", `${error.message} The booking outcome is uncertain. Retry the same booking; do not start a new one.`, true);
    } else {
      pending = null;
      selected.clear();
      status("booking-status", error.status === 409
        ? `${error.message} Availability will refresh; review the seats before trying again.` : error.message, true);
      refreshAvailability(intent.eventId);
    }
  } finally {
    bookingBusy = false;
    renderSelection();
  }
}

$("reserve").addEventListener("click", () => {
  if (pending || !currentUser() || !activeEvent || !selected.size) return;
  if (!globalThis.crypto?.randomUUID) {
    status("booking-status", "Booking requires HTTPS or localhost so a secure request key can be generated.", true);
    return;
  }
  pending = { key: crypto.randomUUID(), eventId: activeEvent.id, eventName: activeEvent.name,
    userId: currentUser().id, email: currentUser().email,
    seatIds: [...selected.keys()].sort(), uncertain: false };
  submitPending();
});
$("retry-booking").addEventListener("click", submitPending);
$("booking-login").addEventListener("click", openAuth);

/** Explain cancellation origin and retain historical labels even when seats are released. */
function reservationCard(booking, details) {
  const card = element("article", undefined, "reservation-card");
  const name = details?.event.name || `Event ${booking.event_id}`;
  const title = element("h3", name);
  const state = booking.cancelled_at
    ? booking.cancellation_reason === "event_cancelled" ? "Cancelled by organizer" : "Cancelled by you"
    : "Confirmed";
  card.append(title, element("span", state, "badge"), element("p", `Booked ${dateLabel(booking.created_at)}`));
  if (details) card.append(element("p", `Event starts ${dateLabel(details.event.starts_at)}`));
  const labels = element("ul");
  for (const id of booking.seat_ids) labels.append(element("li", details?.seats.has(id) ? seatLabel(details.seats.get(id)) : `Seat ${id}`));
  card.append(labels);
  if (!details) card.append(element("p", "Event details could not be loaded. Refresh reservations to try again.", "muted"));
  if (booking.cancelled_at) card.append(element("p", `Cancelled ${dateLabel(booking.cancelled_at)}`));
  else if (details && new Date(details.event.starts_at) <= new Date()) card.append(element("p", "This event has started; cancellation is no longer available.", "muted"));
  else {
    const button = element("button", "Cancel reservation", "secondary");
    button.type = "button";
    button.addEventListener("click", () => confirmCancellation(booking, name));
    card.append(button);
  }
  return card;
}

/** Guard both HTTP and enrichment results against logout, account changes, and newer pages. */
async function loadHistory() {
  const version = ++historyVersion;
  const userId = currentUser()?.id;
  $("history-list").replaceChildren();
  $("history-previous").disabled = $("history-next").disabled = true;
  $("history-refresh").disabled = !userId;
  if (!userId) { status("history-status", "Log in to view your reservations."); return; }
  status("history-status", "Loading reservations…");
  try {
    const bookings = await authRequest(`/users/me/reservations?limit=${historyPageSize}&offset=${historyOffset}`);
    if (version !== historyVersion || currentUser()?.id !== userId) return;
    // Render basic history immediately; optional public metadata must not block
    // seeing or cancelling a booking when a lookup is slow or unavailable.
    const cards = bookings.map(booking => {
      const card = reservationCard(booking, null);
      $("history-list").append(card);
      return card;
    });
    bookings.forEach(async (booking, index) => {
      try {
        const details = await metadata(booking.event_id);
        if (version === historyVersion && currentUser()?.id === userId && cards[index].isConnected) {
          cards[index].replaceWith(reservationCard(booking, details));
        }
      } catch { /* Fallback IDs and cancellation remain usable; Refresh retries metadata. */ }
    });
    status("history-status", bookings.length ? `${bookings.length} reservations on this page.` : "No reservations on this page.");
    $("history-page").textContent = `Page ${historyOffset / historyPageSize + 1}`;
    $("history-previous").disabled = historyOffset === 0;
    $("history-next").disabled = bookings.length < historyPageSize;
  } catch (error) {
    if (version !== historyVersion || currentUser()?.id !== userId) return;
    status("history-status", error.message, true);
    $("history-previous").disabled = historyOffset === 0;
  }
}

let cancellationTarget = null;
let cancellationBusy = false;

/** Explicit confirmation prevents a single accidental click from cancelling a whole booking. */
function confirmCancellation(booking, name) {
  if (cancellationBusy) return;
  cancellationTarget = { booking, userId: currentUser()?.id };
  $("cancel-description").textContent = `Cancel all ${booking.seat_ids.length} seats in your reservation for ${name}? This cannot be undone.`;
  status("cancel-status", "");
  $("confirm-cancel").textContent = "Cancel reservation";
  $("confirm-cancel").disabled = false;
  $("cancel-dialog").showModal();
}

$("confirm-cancel").addEventListener("click", async () => {
  if (cancellationBusy || !cancellationTarget || currentUser()?.id !== cancellationTarget.userId) return;
  const target = cancellationTarget;
  cancellationBusy = true;
  $("confirm-cancel").disabled = true;
  status("cancel-status", "Cancelling reservation…");
  try {
    await authRequest(`/reservations/${target.booking.id}/cancel`, { method: "POST" });
    if (currentUser()?.id === target.userId) {
      $("cancel-dialog").close();
      status("booking-status", "Reservation cancelled. Its seats have been released.");
      refreshAvailability(target.booking.event_id);
      loadHistory();
    }
  } catch (error) {
    if (currentUser()?.id !== target.userId) return;
    // Cancellation is naturally repeatable; retry the same reservation if the response was lost.
    status("cancel-status", !error.status || error.status >= 500
      ? `${error.message} The outcome is uncertain. Retry cancellation of this same reservation.` : error.message, true);
    $("confirm-cancel").textContent = "Retry cancellation";
    refreshAvailability(target.booking.event_id);
    loadHistory();
  } finally {
    cancellationBusy = false;
    $("confirm-cancel").disabled = false;
  }
});
$("keep-reservation").addEventListener("click", () => $("cancel-dialog").close());
$("history-refresh").addEventListener("click", () => { eventCache.clear(); seatCache.clear(); loadHistory(); });
$("history-previous").addEventListener("click", () => { historyOffset -= historyPageSize; loadHistory(); });
$("history-next").addEventListener("click", () => { historyOffset += historyPageSize; loadHistory(); });
// Organizer lifecycle changes invalidate public labels and private cancellation state.
window.addEventListener("historychanged", () => { eventCache.clear(); seatCache.clear(); loadHistory(); });
window.addEventListener("authchange", () => {
  selected.clear();
  ++historyVersion;
  historyOffset = 0;
  if ($("cancel-dialog").open) $("cancel-dialog").close();
  cancellationTarget = null;
  renderSelection();
  loadHistory();
});
// Warn against discarding an unresolved identity; the key intentionally stays out of storage.
window.addEventListener("beforeunload", event => {
  if (pending) { event.preventDefault(); event.returnValue = ""; }
});
renderSelection();
loadHistory();
