const button = document.getElementById("request");
if (button) {
  button.addEventListener("click", async () => {
    button.disabled = true;
    const note = document.getElementById("request-status");
    const response = await fetch(`${location.pathname.replace(/\/$/, "")}/request`, { method: "POST" });
    if (response.ok) {
      button.hidden = true;
      note.className = "status ok";
      note.textContent = "Asked. A new link will arrive as a Slack DM.";
    } else {
      button.disabled = false;
      note.className = "status bad";
      note.textContent = "That didn't go through, try again in a moment.";
    }
  });
}
