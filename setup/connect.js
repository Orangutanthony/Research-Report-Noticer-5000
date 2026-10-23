document.getElementById("connect").addEventListener("submit", async (event) => {
  event.preventDefault();
  const error = document.getElementById("error");
  error.textContent = "";
  const response = await fetch("/setup/api/connect", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key: document.getElementById("key").value }),
  });
  const data = await response.json().catch(() => ({ error: "unexpected reply from the app" }));
  if (!response.ok) {
    error.textContent = data.error || "could not connect";
    return;
  }
  location.href = data.open;
});
