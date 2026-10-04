/** Memory-only bearer authentication; reloading intentionally requires a new login. */
import { ApiError, request } from "./api.js";
import { $, status } from "./ui.js";

let token = null;
let user = null;
let sessionVersion = 0;
let registering = false;
let busy = false;

/** Expose identity without exposing the in-memory bearer token. */
export function currentUser() { return user; }

/** View controllers use this generation to discard results from old sessions. */
export function authVersion() { return sessionVersion; }

/** Announce identity changes so customer views can discard private data immediately. */
function changed() {
  $("account").textContent = user ? "Account" : "Log in / register";
  $("identity").textContent = user ? user.email : "";
  $("logout").hidden = !user;
  window.dispatchEvent(new CustomEvent("authchange", { detail: user }));
}

/** Clear only local authentication; the backend has no token-revocation endpoint. */
export function signOut(message = "Logged out on this device.") {
  token = user = null;
  ++sessionVersion;
  changed();
  status("auth-status", message);
}

/** Expiration from an old request must not invalidate a newer login session. */
export async function authRequest(path, options = {}) {
  if (!token) throw new ApiError("Log in to continue.", 401);
  const version = sessionVersion;
  try {
    return await request(path, { ...options, headers: { ...options.headers, Authorization: `Bearer ${token}` } });
  } catch (error) {
    if (error.status === 401 && version === sessionVersion) {
      signOut("Your session expired. Log in again to continue.");
      openAuth();
    }
    throw error;
  }
}

/** Native modal dialogs provide keyboard focus containment and Escape support. */
export function openAuth() {
  if (user) { status("auth-status", `Logged in as ${user.email}. Account ID: ${user.id}. Refreshing this page logs you out.`); return; }
  if (!$("auth-dialog").open) $("auth-dialog").showModal();
  $("auth-email").focus();
}

function mode(register) {
  registering = register;
  $("auth-title").textContent = register ? "Create an account" : "Welcome back";
  $("auth-submit").textContent = register ? "Register" : "Log in";
  $("auth-password").minLength = register ? 15 : 1;
  $("auth-password").autocomplete = register ? "new-password" : "current-password";
  $("password-help").textContent = register ? "Use 15–128 characters." : "Enter your account password.";
  $("login-mode").setAttribute("aria-pressed", String(!register));
  $("register-mode").setAttribute("aria-pressed", String(register));
  status("auth-form-status", "");
}

/** Registration does not silently log in; successful registration leads to explicit login. */
$("auth-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (busy) return;
  busy = true;
  for (const id of ["auth-submit", "login-mode", "register-mode"]) $(id).disabled = true;
  status("auth-form-status", registering ? "Creating account…" : "Logging in…");
  const credentials = { email: $("auth-email").value, password: $("auth-password").value };
  try {
    if (registering) {
      await request("/auth/register", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(credentials) });
      mode(false);
      status("auth-form-status", "Account created. Log in with your new password.");
      $("auth-password").focus();
    } else {
      const result = await request("/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(credentials) });
      // Verify identity before publishing a session to other views.
      const identity = await request("/users/me", { headers: { Authorization: `Bearer ${result.access_token}` } });
      token = result.access_token;
      user = identity;
      ++sessionVersion;
      changed();
      $("auth-dialog").close();
      status("auth-status", `Logged in as ${identity.email}. Account ID: ${identity.id}. Authentication stays in memory until you refresh or log out.`);
    }
  } catch (error) { status("auth-form-status", error.message, true); }
  finally {
    // Do not retain submitted passwords in the form after any response or failure.
    credentials.password = "";
    $("auth-password").value = "";
    busy = false;
    for (const id of ["auth-submit", "login-mode", "register-mode"]) $(id).disabled = false;
  }
});
$("account").addEventListener("click", openAuth);
$("logout").addEventListener("click", () => signOut());
$("close-auth").addEventListener("click", () => $("auth-dialog").close());
$("login-mode").addEventListener("click", () => mode(false));
$("register-mode").addEventListener("click", () => mode(true));
$("auth-dialog").addEventListener("close", () => { $("auth-password").value = ""; });
mode(false);
