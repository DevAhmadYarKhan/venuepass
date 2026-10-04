/** In-page navigation preserves authentication and unresolved booking identities. */
import { currentUser } from "./auth.js";
import { $, element } from "./ui.js";

/** Switch views without replacing their DOM or clearing booking retry state. */
function navigate() {
  const user = currentUser();
  const route = location.hash.slice(1) || "/events";
  const organizer = route === "/organizer" && user?.is_organizer;
  const venues = route === "/venues" && user?.is_venue_manager;
  $("customer-view").hidden = Boolean(organizer || venues);
  $("organizer-view").hidden = !organizer;
  $("venues-view").hidden = !venues;
  $("organizer-nav").hidden = !user?.is_organizer;
  $("venues-nav").hidden = !user?.is_venue_manager;
  for (const link of $("app-nav").querySelectorAll("a")) {
    if (link.hash.slice(1) === route) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  if ((route === "/organizer" && !organizer) || (route === "/venues" && !venues)) {
    location.hash = "/events";
    return;
  }
  if (route === "/reservations") $("history-heading").focus();
  const match = route.match(/^\/events\/([0-9a-f-]{36})$/i);
  window.dispatchEvent(new CustomEvent("routechange", {
    detail: { route, eventId: match?.[1] || null },
  }));
}

/** Clear private screen contents before any new account can open them. */
window.addEventListener("authchange", () => {
  for (const id of ["organizer-content", "venues-content"]) {
    $(id).replaceChildren(element("p", "Choose a management view to continue."));
  }
  navigate();
});
window.addEventListener("hashchange", navigate);
navigate();
