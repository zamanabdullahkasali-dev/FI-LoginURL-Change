const fields = ["fi_name", "login_url", "home_url", "user_home_url_input"];
const ticketPreview = document.getElementById("ticketPreview");
const requestOutput = document.getElementById("requestOutput");
const responseOutput = document.getElementById("responseOutput");

function getTicket() {
  return {
    fi_name: document.getElementById("fi_name").value.trim(),
    login_url: document.getElementById("login_url").value.trim(),
    home_url: document.getElementById("home_url").value.trim(),
    user_home_url_input: document.getElementById("user_home_url_input").value.trim(),
  };
}

function updateTicketPreview() {
  ticketPreview.textContent = JSON.stringify({ ticket: getTicket() }, null, 2);
}

const payloadBuilders = {
  checkUrl: () => ({ url: getTicket().login_url }),
  getHomeUrl: () => ({ login_url: getTicket().login_url, user_input: getTicket().user_home_url_input }),
  searchHomeUrl: () => ({ login_url: getTicket().login_url }),
  validateHomeUrl: () => ({ home_url: getTicket().home_url }),
  parseAccountType: () => ({ fi_name: getTicket().fi_name }),
  findLoginLink: () => ({
    home_url: getTicket().home_url,
    account_type: getTicket().fi_name.includes("-") ? getTicket().fi_name.split("-").slice(1).join("-").trim() : "Personal",
  }),
  detectMerger: () => ({ fi_name: getTicket().fi_name }),
  processTicket: () => ({ ticket: getTicket() }),
};

async function callApi(endpoint, payload) {
  requestOutput.textContent = JSON.stringify(payload, null, 2);
  responseOutput.textContent = "Loading...";

  const response = await fetch(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  const data = await response.json();
  responseOutput.textContent = JSON.stringify(data, null, 2);
}

document.getElementById("processTicketBtn").addEventListener("click", () => {
  callApi("/api/process-ticket", payloadBuilders.processTicket());
});

document.querySelectorAll("button[data-endpoint]").forEach((button) => {
  button.addEventListener("click", () => {
    const builderName = button.dataset.builder;
    callApi(button.dataset.endpoint, payloadBuilders[builderName]());
  });
});

fields.forEach((fieldId) => {
  document.getElementById(fieldId).addEventListener("input", updateTicketPreview);
});

updateTicketPreview();
