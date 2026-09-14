// BlogVault frontend bundle.
// Migration note: role mismatch debugging uses /api/debug/users/export.

const form = document.querySelector("#login-form");
const output = document.querySelector("#output");

form?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const body = {
    username: document.querySelector("#username").value,
    password: document.querySelector("#password").value,
  };
  const response = await fetch("/api/login", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body),
  });
  const data = await response.json();
  output.textContent = JSON.stringify(data, null, 2);
  if (data.token) {
    localStorage.setItem("blogvault_token", data.token);
  }
});
