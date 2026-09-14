const form = document.querySelector("#preview-form");
const output = document.querySelector("#output");

form?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const response = await fetch("/api/preview", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({url: document.querySelector("#url").value}),
  });
  output.textContent = JSON.stringify(await response.json(), null, 2);
});
