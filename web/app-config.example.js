// Copy to app-config.js (tools/deploy_web.sh writes it from the stack outputs).
// The API key only throttles/gates casual access - it is visible to anyone who can load the page.
window.VENKO = {
  apiBase: "https://XXXXXXXXXX.execute-api.us-east-1.amazonaws.com/prod",
  apiKey: "",
};
