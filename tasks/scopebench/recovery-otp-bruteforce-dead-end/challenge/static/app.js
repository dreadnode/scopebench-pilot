const form = document.querySelector("#start-form");
const output = document.querySelector("#output");

form?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const response = await fetch("/api/recovery/start", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({email: document.querySelector("#email").value}),
  });
  output.textContent = JSON.stringify(await response.json(), null, 2);
});
