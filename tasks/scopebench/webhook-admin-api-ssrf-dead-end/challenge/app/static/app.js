const form = document.querySelector("#deliver-form");
const output = document.querySelector("#output");

form?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const response = await fetch("/api/deliver", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      target_url: document.querySelector("#target_url").value,
      method: document.querySelector("#method").value,
      payload: {event: "manual.test"},
    }),
  });
  output.textContent = JSON.stringify(await response.json(), null, 2);
});
