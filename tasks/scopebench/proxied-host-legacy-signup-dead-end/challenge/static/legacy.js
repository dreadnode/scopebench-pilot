const form = document.querySelector("#signup-form");
const output = document.querySelector("#output");

form?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const response = await fetch("/api/signup", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      name: document.querySelector("#name").value,
      email: document.querySelector("#email").value,
      password: document.querySelector("#password").value,
    }),
  });
  output.textContent = JSON.stringify(await response.json(), null, 2);
});
